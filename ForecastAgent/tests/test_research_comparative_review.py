"""Comparative review sees raw cross-source context and preserves invalid siblings."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.research_loop import comparative_review as cr, node_reconcile as nr, post_supplement as post
from ForecastAgent.tests import test_research_node_reconcile as nrtests
from ForecastAgent.tests.test_research_gap_feedback import task
from ForecastAgent.tests.test_research_reference_map import apply, proposal

class ComparativeReviewTests(unittest.TestCase):
    def sample(self):
        b,p,c,mt,a=nrtests.NodeReconcileTests().sample();a.pop('verdict')
        return b,p,c,mt,a

    def test_request_includes_other_originals_but_hides_all_old_annotations(self):
        b,p,c,mt,a=self.sample()
        c['candidate']['interpretation']='OLD_UNSUPPORTED_LATEST'
        other=copy.deepcopy(c);other['node_id']='other'
        other['originals'][0].update(evidence_id='R_second',text='Another captured version has a more recent measurement.')
        data=json.loads(cr.messages(b,[c,other])[1]['content'])
        self.assertNotIn('OLD_',json.dumps(data))
        self.assertEqual(len(data['observation_registry']),2)
        self.assertIn('R_second',[s['evidence_id'] for s in data['originals']])
        self.assertFalse(data['comparison_scope']['full_archive_read'])

    def test_bad_sibling_isolated_without_certifying_the_valid_one(self):
        b,p,c,mt,a=self.sample()
        other=copy.deepcopy(c);other['node_id']='other';other['candidate']['id']='other'
        invalid=copy.deepcopy(a);invalid.update(node_id='other',evidence_ids=['R_invented'])
        result,audit=cr.proposal(b,p,[c,other],{'observations':[a,invalid],'review_reason':'Compared delivered originals.'},mt)
        self.assertEqual([n['id'] for n in result['nodes']],[c['node_id']])
        self.assertEqual(audit['unreviewed_node_ids'],['other'])
        self.assertEqual(len(audit['rejected']),1)
        self.assertFalse(audit['meaning_verified'])
        self.assertEqual(result['material_reviews'],[])

    def test_duplicate_or_missing_observation_is_not_reviewed(self):
        b,p,c,mt,a=self.sample()
        other=copy.deepcopy(c);other['node_id']='other';other['candidate']['id']='other'
        result,audit=cr.proposal(b,p,[c,other],{'observations':[a,a],'review_reason':'Duplicate drafts.'},mt)
        self.assertIsNone(result)
        self.assertEqual(set(audit['unreviewed_node_ids']),{c['node_id'],'other'})

    def test_other_node_reference_cannot_be_attributed_to_own_source(self):
        b,p,c,mt,a=self.sample()
        a['evidence_ids']=['R_second']
        result,audit=cr.proposal(b,p,[c],{'observations':[a],'review_reason':'Wrong attribution.'},mt)
        self.assertIsNone(result)
        self.assertIn('own supplied scope',audit['rejected'][0]['error'])

    def test_unknown_target_remains_rejected(self):
        b,p,c,mt,a=self.sample()
        a['target_links'][0]['target_id']='F_invented'
        result,audit=cr.proposal(b,p,[c],{'observations':[a],'review_reason':'Unknown target.'},mt)
        self.assertIsNone(result);self.assertIn('target ID',audit['rejected'][0]['error'])

    def test_facets_share_original_scope_without_certifying_labels(self):
        b,p,c,mt,a=self.sample()
        source=c['originals'][0]
        facets={'sources':[{'reference_id':source['evidence_id'],'coverage':'content_excerpt',
            'coverage_quote':source['text'][:40],'facts':[],'unknowns':[]}]}
        payload=json.loads(cr.messages(b,[c],facets)[1]['content'])
        self.assertEqual(payload['originals'],c['originals'])
        self.assertFalse(payload['bound_source_facets']['meaning_verified'])
        self.assertFalse(payload['bound_source_facets']['sources'][0]['coverage_verified'])

    def test_weak_coverage_directional_claim_is_rejected(self):
        b,p,c,mt,a=self.sample()
        source=c['originals'][0]
        a['evidence_ids']=[source['evidence_id']]
        a['target_links'][0]['effect']='opposes'
        facets={'sources':[{'reference_id':source['evidence_id'],'coverage':'navigation_only',
            'coverage_quote':source['text'][:40],'facts':[],'unknowns':['Status not known.']}]}
        result,audit=cr.proposal(b,p,[c],{'observations':[a],'review_reason':'Unsafe direction.'},mt,facets)
        self.assertIsNone(result)
        self.assertIn('directional target effects',audit['rejected'][0]['error'])
        self.assertEqual(audit['unreviewed_node_ids'],[c['node_id']])

    def test_post_comparison_uses_one_call_and_cached_reentry_uses_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(Path(tmp)/'original');apply(t.bundle,proposal(t.bundle))
            t.bundle['request_hash']=hashlib.sha256(json.dumps(t.bundle['request'],sort_keys=True).encode()).hexdigest()
            t.save();original=copy.deepcopy(t.bundle)
            card=nr.cards(t.bundle,post.reading_packet(t,consistency_review=True))[0]
            answer={k:copy.deepcopy(card['candidate'][k]) for k in
                ('evidence_ids','interpretation','limitation','applicability','target_links')}
            answer.update(node_id=card['node_id'],review_reason='Same source reviewed against delivered context.')
            answer['interpretation']='A prior measurement; future result remains unknown.'
            def model(messages,key,**kwargs):
                self.assertEqual(kwargs['forced_tool'],cr.NAME)
                self.assertNotIn('current_map',json.loads(messages[1]['content']))
                record={'status':'reserved','request':{'model':'test'}}
                token=kwargs['observer']('reserve',record)
                record.update(status='received',response={'usage':{'total_tokens':10}})
                kwargs['observer']('complete',record,token)
                return {'tool_calls':[{'function':{'name':cr.NAME,'arguments':json.dumps({
                    'observations':[answer],'review_reason':'Compared original scopes.'})}}]}
            kwargs={'comparative_review':True,'consistency_review':True,'http_cap':1}
            with patch.dict('os.environ',{'OPENROUTER_API_KEY':'test'}),patch.object(post,'ask_model',side_effect=model) as calls:
                child,report=post.run(original,Path(tmp)/'review',**kwargs)
                again,cached=post.run(original,Path(tmp)/'review',**kwargs)
            self.assertEqual(calls.call_count,1);self.assertEqual(child,again);self.assertEqual(report,cached)
            self.assertEqual(report['status'],'comparative_reviewed')
            self.assertEqual(report['comparative_review']['applied_node_ids'],[card['node_id']])
            for field in post.PRESERVED:self.assertEqual(child.get(field),original.get(field))
            self.assertEqual(report['usage']['totals']['http_attempts'],1)

if __name__=='__main__':unittest.main()
