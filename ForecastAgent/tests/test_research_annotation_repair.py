"""Formatting recovery consumes shared HTTP allowance and freezes evidence IDs."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.research_loop import node_reconcile as nr, post_supplement as post
from ForecastAgent.tests import test_research_node_reconcile as nrtests
from ForecastAgent.tests.test_research_gap_feedback import task
from ForecastAgent.tests.test_research_reference_map import apply, proposal


class AnnotationRepairTests(unittest.TestCase):
    def test_format_feedback_reports_all_lengths_and_enum_without_clipping(self):
        b,packet,card,mt,answer=nrtests.NodeReconcileTests().sample()
        answer.pop('verdict');answer.update(interpretation='x'*241,limitation='y'*181,applicability='baseline')
        feedback=nr.format_feedback(b,card,answer,mt,blind=True)
        self.assertEqual(len(feedback['errors']),3)
        self.assertEqual(feedback['draft'],answer)
        self.assertEqual(len(feedback['draft']['interpretation']),241)
        self.assertFalse(feedback['semantic_correctness_verified'])

    def test_invented_reference_or_target_is_not_a_format_repair(self):
        b,packet,card,mt,answer=nrtests.NodeReconcileTests().sample()
        answer.pop('verdict');answer['interpretation']='x'*241
        bad=copy.deepcopy(answer);bad['evidence_ids']=['R_invented']
        self.assertIsNone(nr.format_feedback(b,card,bad,mt,blind=True))
        bad=copy.deepcopy(answer);bad['target_links'][0]['target_id']='F_invented'
        self.assertIsNone(nr.format_feedback(b,card,bad,mt,blind=True))

    def test_repaired_reference_or_target_change_is_rejected(self):
        b,packet,card,mt,answer=nrtests.NodeReconcileTests().sample()
        changed=copy.deepcopy(answer);changed['evidence_ids']=[]
        with self.assertRaisesRegex(ValueError,'frozen evidence_ids'):
            nr.verify_repair_core(answer,changed)
        changed=copy.deepcopy(answer);changed['target_links']=[]
        with self.assertRaisesRegex(ValueError,'frozen target IDs'):
            nr.verify_repair_core(answer,changed)

    def exercise(self,*,cap=2,seeded=False,wrong_core=False,always_bad=False):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(Path(tmp)/'original');apply(t.bundle,proposal(t.bundle))
            t.bundle['request_hash']=hashlib.sha256(json.dumps(t.bundle['request'],sort_keys=True).encode()).hexdigest()
            t.save();original=copy.deepcopy(t.bundle)
            card=nr.cards(t.bundle,post.reading_packet(t,consistency_review=True))[0]
            answer={k:copy.deepcopy(card['candidate'][k]) for k in
                ('evidence_ids','interpretation','limitation','applicability','target_links')}
            answer.update(node_id=card['node_id'],review_reason='Source-local annotation.')
            bad=copy.deepcopy(answer);bad['limitation']='x'*181
            sent=[]
            def model(messages,key,**kwargs):
                payload=json.loads(messages[1]['content']);sent.append(payload)
                reply=copy.deepcopy(bad if always_bad or not seeded and len(sent)==1 else answer)
                if wrong_core and (seeded or len(sent)==2):reply['evidence_ids']=[]
                record={'status':'reserved','request':{'model':'test'}}
                token=kwargs['observer']('reserve',record)
                record.update(status='received',response={'usage':{'total_tokens':10}})
                kwargs['observer']('complete',record,token)
                return {'tool_calls':[{'function':{'name':nr.NAME,'arguments':json.dumps(reply)}}]}
            kwargs={'node_by_node':True,'blind_node_read':True,'consistency_review':True,
                'http_cap':cap,'node_format_retries':1,
                'annotation_repair_seeds':{card['node_id']:bad} if seeded else None}
            with patch.dict('os.environ',{'OPENROUTER_API_KEY':'test'}),patch.object(post,'ask_model',side_effect=model) as calls:
                child,report=post.run(original,Path(tmp)/'review',**kwargs)
                again,cached=post.run(original,Path(tmp)/'review',**kwargs)
            self.assertEqual(child,again);self.assertEqual(report,cached)
            self.assertEqual(calls.call_count,report['usage']['totals']['http_attempts'])
            self.assertLessEqual(calls.call_count,cap)
            for field in post.PRESERVED:self.assertEqual(child.get(field),original.get(field))
            return report,sent

    def test_live_format_retry_shares_cap_and_does_not_repeat_on_reentry(self):
        report,sent=self.exercise()
        self.assertEqual(report['usage']['totals']['http_attempts'],2)
        self.assertEqual(report['status'],'nodes_reviewed')
        self.assertTrue(report['steps'][0]['node_review']['annotation_format_repair_used'])
        self.assertNotIn('annotation_format_repair',sent[0])
        self.assertEqual(sent[1]['annotation_format_repair']['draft']['limitation'],'x'*181)

    def test_no_extra_retry_after_http_cap_or_second_invalid_output(self):
        report,_=self.exercise(cap=1)
        self.assertEqual(report['usage']['totals']['http_attempts'],1)
        self.assertEqual(report['status'],'proposal_rejected')
        report,_=self.exercise(always_bad=True)
        self.assertEqual(report['usage']['totals']['http_attempts'],2)
        self.assertEqual(report['status'],'proposal_rejected')

    def test_saved_rejected_draft_repair_uses_only_one_new_call(self):
        report,sent=self.exercise(seeded=True)
        self.assertEqual(report['usage']['totals']['http_attempts'],1)
        self.assertTrue(report['annotation_repair_only_requested'])
        self.assertEqual(report['status'],'nodes_reviewed')
        self.assertNotIn('candidate',sent[0]['observation_to_review'])

    def test_model_cannot_replace_references_during_format_repair(self):
        report,_=self.exercise(seeded=True,wrong_core=True)
        self.assertEqual(report['status'],'proposal_rejected')
        self.assertIn('frozen evidence_ids',report['steps'][0]['error'])
        self.assertEqual(report['usage']['totals']['http_attempts'],1)


if __name__=='__main__':unittest.main()
