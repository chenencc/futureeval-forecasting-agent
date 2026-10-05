"""Bounded actual decision journals, source-view novelty and label separation."""
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition import handoff, handoff_scoring as trial
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import digest, load
from ForecastAgent.competition.mercury import packet_for
from ForecastAgent.providers import decisions
from ForecastAgent.tests.test_material_handoff import fixture
from ForecastAgent.tests.test_competition_mercury import response


def decide(calls, *, uncertain=False, fail_stage=None):
    def run(state,questions,key,observer):
        record={'endpoint':decisions.ENDPOINT,'request':{'model':decisions.MODEL,'state':state,'questions':questions},'status':'reserved'}
        token=observer('reserve',record);calls.append(copy.deepcopy(state))
        if len(calls)==fail_stage:
            record.update(status='http_error',http_status=503);observer('complete',record,token)
            raise RuntimeError('Decision endpoint HTTP 503')
        answer=response(questions,insufficient=uncertain)
        answer['answers']['event_yes']['noul']=.3 if len(calls)==1 else .1
        answer['usage']={'total_tokens':10}
        record.update(status='received',http_status=200,response=answer)
        observer('complete',record,token)
        return answer
    return run


class HandoffScoringTests(TestCase):
    def test_new_ids_and_overlapping_ranges_are_not_new_evidence(self):
        before={'evidence':[{'source_id':'S1','start':0,'end':100,'evidence_id':'E1'}]}
        after={'evidence':[{'source_id':'S1','start':0,'end':100,'evidence_id':'H1'},
                            {'source_id':'S1','start':20,'end':100,'evidence_id':'H2'}]}
        self.assertEqual(trial.novel_chars(before,after),0)
        after['evidence'].append({'source_id':'S1','start':90,'end':130})
        self.assertEqual(trial.novel_chars(before,after),30)
        after['evidence'].append({'source_id':'S1','document_index':2,'start':0,'end':40})
        self.assertEqual(trial.novel_chars(before,after),70)

    def test_conditional_proposals_preserve_first_text_and_exact_document_views(self):
        bundle=fixture(('Original Alpha Beta report evidence.\n'*1200))
        first,_=handoff.pack(bundle)
        second,audit=trial.extend(bundle,packet_for(bundle),first,['observation_coverage'])
        self.assertLessEqual(chain.request_bytes(second),chain.SECOND_BYTES)
        self.assertEqual(trial.audit_spans(bundle,second),[])
        self.assertEqual(audit['novel_coordinate_chars'],trial.novel_chars(first,second))
        self.assertGreater(audit['novel_coordinate_chars'],900)

    def test_first_only_success_has_durable_reuse_and_never_opens_labels(self):
        bundle=fixture('Original Alpha amount 27 and Beta amount 18.\n');calls=[]
        original_load=trial.load
        def guarded(path):
            if Path(path)==trial.LABELS:raise AssertionError('Labels opened during inference')
            return original_load(path)
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}), \
             patch('ForecastAgent.providers.decisions.decide',decide(calls)), \
             patch.object(trial,'load',side_effect=guarded),patch('ForecastAgent.providers.http.download') as fetch:
            result=trial.run_route(bundle,Path(tmp),'new_packing',trial.protocol(),'test-bundle')
            again=trial.run_route(bundle,Path(tmp),'new_packing',trial.protocol(),'test-bundle')
            self.assertEqual(result,again)
            self.assertEqual(len(calls),1)
            self.assertEqual(result['probability_yes'],.3)
            self.assertFalse(result['routing']['second_call_required'])
            fetch.assert_not_called()

    def test_second_failure_preserves_first_and_does_not_reset_either_cap(self):
        bundle=fixture('Original Alpha Beta report.\n'*1200);calls=[]
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}), \
             patch('ForecastAgent.providers.decisions.decide',decide(calls,uncertain=True,fail_stage=2)):
            result=trial.run_route(bundle,Path(tmp),'new_packing',trial.protocol(),'test-bundle')
            again=trial.run_route(bundle,Path(tmp),'new_packing',trial.protocol(),'test-bundle')
            self.assertEqual(result,again);self.assertEqual(len(calls),2)
            self.assertEqual(result['status'],'completed_with_reread_failure')
            self.assertEqual(result['first_probability_yes'],result['probability_yes'])
            self.assertEqual(len(list(Path(tmp).glob('*/http/*.json'))),2)

    def test_first_failure_cannot_become_a_synthetic_score_or_be_retried(self):
        bundle=fixture('Original report.\n');calls=[]
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}), \
             patch('ForecastAgent.providers.decisions.decide',decide(calls,fail_stage=1)):
            result=trial.run_route(bundle,Path(tmp),'old_packing',trial.protocol(),'test-bundle')
            again=trial.run_route(bundle,Path(tmp),'old_packing',trial.protocol(),'test-bundle')
            self.assertEqual(result,again);self.assertEqual(len(calls),1)
            self.assertEqual(result['status'],'failed');self.assertIsNone(result['probability_yes'])

    def test_new_question_or_modified_sealed_response_is_rejected(self):
        bundle=fixture('Original report.\n');calls=[]
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}), \
             patch('ForecastAgent.providers.decisions.decide',decide(calls)):
            trial.run_route(bundle,Path(tmp),'old_packing',trial.protocol(),'test-bundle')
            changed=copy.deepcopy(bundle);changed['request']['question']='Changed question'
            with self.assertRaisesRegex(ValueError,'Frozen scoring'):
                trial.run_route(changed,Path(tmp),'old_packing',trial.protocol(),'test-bundle')
            p=Path(tmp)/'first/response.json';p.write_text('{}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'response changed'):
                trial.run_route(bundle,Path(tmp),'old_packing',trial.protocol(),'test-bundle')

    def test_scores_use_raw_and_release_clipped_probabilities_separately(self):
        result=trial.scores(1.,0.)
        self.assertEqual(result['raw_brier'],1.)
        self.assertEqual(result['clipped_probability'],.98)
        self.assertAlmostEqual(result['clipped_brier'],.9604)
        self.assertFalse(result['correct_at_half'])
        self.assertGreater(result['raw_log_loss'],result['clipped_log_loss'])

    def test_fake_provider_cannot_bypass_a_preexisting_first_reservation(self):
        bundle=fixture('Original report.\n')
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}):
            p=Path(tmp)/'first/http/001.json';p.parent.mkdir(parents=True);p.write_text('{"status":"reserved"}')
            with patch('ForecastAgent.providers.decisions.urlopen') as network:
                result=trial.run_route(bundle,Path(tmp),'old_packing',trial.protocol(),'test-bundle')
            self.assertIsNone(result['probability_yes']);network.assert_not_called()
            self.assertIn('cap exhausted',result['error'])

    def test_review_scores_only_sealed_actual_requests_and_flags_tampered_journal(self):
        bundle=fixture('Original Alpha and Beta report.\n');calls=[]
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}), \
             patch('ForecastAgent.providers.decisions.decide',decide(calls)):
            root=Path(tmp);source=root/'source/123/candidate/bundle.json';source.parent.mkdir(parents=True)
            raw=json.dumps(bundle).encode();source.write_bytes(raw);source_sha=hashlib.sha256(raw).hexdigest()
            labels=root/'labels.json';labels.write_text(json.dumps({'records':{'123':{'resolution':0.}}}))
            frozen=trial.protocol();frozen.update(question_ids=['123'],bundles={'123':source_sha},labels_sha256_lf=trial.text_sha(labels))
            output=root/'score'
            for variant in ('old_packing','new_packing'):
                trial.run_route(bundle,output/'123'/variant,variant,frozen,source_sha)
            with patch.object(trial,'protocol',return_value=frozen),patch.object(trial,'LABELS',labels):
                report=trial.review(output,root/'source')
                self.assertTrue(report['complete']);self.assertEqual(report['integrity_errors'],[])
                self.assertEqual(report['metrics']['first']['scored_pairs'],1)
                self.assertEqual(report['consumption']['actual_mercury_http_attempts'],2)
                self.assertEqual(report['consumption']['known_reported_tokens'],20)
                self.assertEqual(report['rows'][0]['resolution'],0.)
                journal=output/'123/old_packing/first/http/001.json';record=load(journal)
                record['request']['state']['question']['question']='Altered actual request'
                journal.write_text(json.dumps(record))
                report=trial.review(output,root/'source')
                self.assertTrue(any('Actual request differs' in e.get('reason','') for e in report['integrity_errors']))

    def test_missing_routes_remain_visible_and_are_not_counted_as_zero_brier(self):
        bundle=fixture('Original report.\n')
        with TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source/123/candidate/bundle.json';source.parent.mkdir(parents=True)
            raw=json.dumps(bundle).encode();source.write_bytes(raw)
            labels=root/'labels.json';labels.write_text(json.dumps({'records':{'123':{'resolution':1.}}}))
            frozen=trial.protocol();frozen.update(question_ids=['123'],bundles={'123':hashlib.sha256(raw).hexdigest()},labels_sha256_lf=trial.text_sha(labels))
            with patch.object(trial,'protocol',return_value=frozen),patch.object(trial,'LABELS',labels):
                report=trial.review(root/'empty',root/'source')
            self.assertFalse(report['complete'])
            self.assertEqual(report['metrics']['first']['scored_pairs'],0)
            self.assertIsNone(report['metrics']['first']['routes']['new_packing']['clipped_brier'])
            self.assertEqual(report['rows'][0]['routes']['old_packing']['status'],'missing')
