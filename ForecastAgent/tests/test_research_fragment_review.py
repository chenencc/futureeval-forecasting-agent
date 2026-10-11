"""Selection uses original text identity, not generated quotes or known labels."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.research_loop import fragment_review as fr, node_reconcile as nr, post_supplement as post
from ForecastAgent.tests import test_research_node_reconcile as nrtests
from ForecastAgent.tests.test_research_gap_feedback import task
from ForecastAgent.tests.test_research_reference_map import apply, proposal


class FragmentReviewTests(unittest.TestCase):
    def sample(self):
        b,p,c,mt,a=nrtests.NodeReconcileTests().sample()
        _,units,rows=fr.registry([c])
        ident=rows[0]['selectable_fragment_ids'][0]
        a={'node_id':c['node_id'],'primary_fragment_id':ident,
            'selections':[{'fragment_id':ident,'role':'event_time'}],
            'applicability':'background','target_links':[]}
        return b,p,c,mt,a

    def test_lossless_partition_and_long_units_explicitly_partial(self):
        text='A first sentence. Next line.\r\n'+('word '*100)+'\n'+json.dumps({'date':'2026-10-09','close':12266})
        result=fr.units([{'evidence_id':'R','body_sha256':'body','text':text}])[0]
        self.assertEqual(''.join(f['text'] for f in result['fragments']),text)
        self.assertTrue(any(f['partial_unit'] for f in result['fragments']))
        self.assertTrue(all(len(f['text'])<=220 for f in result['fragments']))
        for f in result['fragments']:self.assertEqual(text[f['local_start']:f['local_end']],f['text'])

    def test_full_table_rows_stay_intact_when_bounded(self):
        rows=[json.dumps({'date':'2025-10-17','close':10208.8}),json.dumps({'date':'2026-10-09','close':12266})]
        fragments=fr.units([{'evidence_id':'R','text':'\n'.join(rows)}])[0]['fragments']
        self.assertEqual([f['text'].rstrip('\n') for f in fragments],rows)

    def test_literal_replaces_observation_and_old_event_stage_not_inherited(self):
        b,p,c,mt,a=self.sample()
        args,audit=fr.proposal(b,p,[c],{'observations':[a],'review_reason':'Select own original.'},mt)
        selected=audit['fragment_selections'][0]['selected_fragments'][0]
        self.assertEqual(args['nodes'][0]['interpretation'],selected['text'])
        self.assertNotIn('event_time',args['nodes'][0])
        self.assertNotIn('time_status',args['nodes'][0])
        self.assertNotIn('event_stage',args['nodes'][0])
        self.assertFalse(audit['event_roles_verified'])

    def test_generated_quote_fields_not_allowed(self):
        b,p,c,mt,a=self.sample();a['interpretation']='Generated event date.'
        args,audit=fr.proposal(b,p,[c],{'observations':[a],'review_reason':'Bad paraphrase.'},mt)
        self.assertIsNone(args);self.assertEqual(len(audit['fragment_selection_rejections']),1)

    def test_cross_node_unit_cannot_be_attributed_to_own_source(self):
        b,p,c,mt,a=self.sample();other=copy.deepcopy(c);other['node_id']='second';other['candidate']['id']='second'
        other['originals']=[{**c['originals'][0],'evidence_id':'R_other','text':'A different source.'}]
        other['selectable_reference_ids']=['R_other']
        _,_,rows=fr.registry([c,other]);ident=rows[1]['selectable_fragment_ids'][0]
        a['primary_fragment_id']=ident;a['selections'][0]['fragment_id']=ident
        args,audit=fr.proposal(b,p,[c,other],{'observations':[a],'review_reason':'Wrong source.'},mt)
        self.assertIsNone(args);self.assertIn('cross-node',audit['fragment_selection_rejections'][0]['error'])

    def test_context_only_direction_rejected_without_certifying_role(self):
        b,p,c,mt,a=self.sample();a['selections'][0]['role']='publication_time'
        original=nrtests.NodeReconcileTests().sample()[4]
        a['target_links']=original['target_links'];a['target_links'][0]['effect']='opposes'
        args,audit=fr.proposal(b,p,[c],{'observations':[a],'review_reason':'Date is not an event.'},mt)
        self.assertIsNone(args);self.assertIn('directional',audit['fragment_selection_rejections'][0]['error'])

    def test_request_hides_old_notes_and_preserves_all_source_text(self):
        b,p,c,mt,a=self.sample();c['candidate']['interpretation']='OLD_WRONG_CLAIM'
        data=json.loads(fr.messages(b,[c])[1]['content'])
        self.assertNotIn('OLD_WRONG_CLAIM',json.dumps(data))
        self.assertEqual(''.join(f['text'] for f in data['originals'][0]['fragments']),c['originals'][0]['text'])
        self.assertFalse(data['fragment_scope']['roles_verified'])

    def test_post_fragment_mode_one_receipt_and_cached_reentry(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(Path(tmp)/'original');apply(t.bundle,proposal(t.bundle))
            t.bundle['request_hash']=hashlib.sha256(json.dumps(t.bundle['request'],sort_keys=True).encode()).hexdigest();t.save()
            before=copy.deepcopy(t.bundle)
            def model(messages,key,**kwargs):
                self.assertEqual(kwargs['forced_tool'],fr.NAME)
                data=json.loads(messages[1]['content']);row=data['observation_registry'][0]
                ident=next(s['fragments'][0]['fragment_id'] for s in data['originals']
                    if s['evidence_id'] in row['selectable_reference_ids'] and s['fragments'])
                answer={'observations':[{'node_id':row['node_id'],'primary_fragment_id':ident,
                    'selections':[{'fragment_id':ident,'role':'context'}],
                    'applicability':'background','target_links':[]}],'review_reason':'Original text selection.'}
                record={'status':'reserved','request':{'model':'test'}};token=kwargs['observer']('reserve',record)
                record.update(status='received',response={'usage':{'total_tokens':10}})
                kwargs['observer']('complete',record,token)
                return {'tool_calls':[{'function':{'name':fr.NAME,'arguments':json.dumps(answer)}}]}
            params={'comparative_review':True,'consistency_review':True,'fragment_selection':True,'http_cap':1}
            with patch.dict('os.environ',{'OPENROUTER_API_KEY':'test'}),patch.object(post,'ask_model',side_effect=model) as calls:
                child,report=post.run(before,Path(tmp)/'review',**params)
                again,cached=post.run(before,Path(tmp)/'review',**params)
            self.assertEqual(calls.call_count,1);self.assertEqual(child,again);self.assertEqual(report,cached)
            self.assertTrue(report['fragment_selection_requested'])
            self.assertEqual(report['usage']['totals']['http_attempts'],1)
            for node in child['research_loop']['current']['nodes']:
                self.assertEqual(node['event_time'],'')
                self.assertEqual(node['time_status'],'unknown')
                self.assertEqual(node['event_stage'],'unknown')
            for key in post.PRESERVED:self.assertEqual(child.get(key),before.get(key))


if __name__=='__main__':unittest.main()
