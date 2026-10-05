"""Fresh-source isolation and controlled reread transport accounting."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition import full_chain_ten as trial
from ForecastAgent.analysis.pilot import load, save
from ForecastAgent.tests.test_material_handoff import fixture
from ForecastAgent.tests.test_handoff_scoring import decide


class FullChainTenTests(TestCase):
    def test_cohort_and_release_are_frozen_and_outcomes_are_absent(self):
        p,c=trial.protocol()
        self.assertEqual(len(c['requests']),10)
        self.assertEqual(len(set(c['question_ids'])),10)
        self.assertFalse(set(c['question_ids']) & set(c['excluded_current_repair_ids']))
        for r in c['requests']:trial.pipeline.reject_outcomes(r)
        self.assertEqual(p['decision_http_ceiling'],40)

    def test_second_probe_runs_when_release_has_no_gaps_and_is_kept_separate(self):
        b=fixture('Original Alpha Beta report.\n'*1200); calls=[]
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}), \
             patch('ForecastAgent.providers.decisions.decide',decide(calls)):
            out=trial.score_route(b,Path(tmp),'new_packing',{'minimum_new_chars':900})
            self.assertEqual(len(calls),2)
            self.assertFalse(out['routing']['release_would_reread'])
            self.assertEqual(out['first_probability_yes'],.3)
            self.assertEqual(out['reread_probability_yes'],.1)
            self.assertEqual(out['production_probability_yes'],.3)
            self.assertEqual(trial.audit_spans(b,calls[1]),[])

    def test_failed_probe_keeps_first_and_resume_cannot_renew_attempts(self):
        b=fixture('Original Alpha Beta report.\n'*1200); calls=[]
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}), \
             patch('ForecastAgent.providers.decisions.decide',decide(calls,fail_stage=2)):
            out=trial.score_route(b,Path(tmp),'old_packing',{'minimum_new_chars':900})
            again=trial.score_route(b,Path(tmp),'old_packing',{'minimum_new_chars':900})
            self.assertEqual(out,again);self.assertEqual(len(calls),2)
            self.assertEqual(out['probability_yes'],.3)
            self.assertIsNone(out['reread_probability_yes'])

    def test_second_preparation_failure_also_preserves_first(self):
        b=fixture('Original Alpha Beta report.\n');calls=[]
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}), \
             patch('ForecastAgent.providers.decisions.decide',decide(calls)), \
             patch.object(trial,'reread_state',side_effect=ValueError('Invalid second input')):
            out=trial.score_route(b,Path(tmp),'new_packing',{'minimum_new_chars':900})
            self.assertEqual(out['probability_yes'],.3)
            self.assertEqual(out['status'],'completed_with_reread_failure')

    def test_short_source_is_not_falsely_counted_as_reread(self):
        b=fixture('Original Alpha Beta report.\n');calls=[]
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}), \
             patch('ForecastAgent.providers.decisions.decide',decide(calls)):
            out=trial.score_route(b,Path(tmp),'new_packing',{'minimum_new_chars':900})
            self.assertEqual(len(calls),1)
            self.assertIsNone(out['reread_probability_yes'])
            self.assertEqual(out['reread_status'],'not_enough_new_original_text')

    def test_labels_are_never_loaded_in_score_inference(self):
        b=fixture('Original Alpha Beta report.\n');calls=[];original=trial.load
        def guard(path):
            if Path(path)==trial.LABELS:raise AssertionError('Label read during inference')
            return original(path)
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'OPENROUTER_API_KEY':'offline'}), \
             patch('ForecastAgent.providers.decisions.decide',decide(calls)),patch.object(trial,'load',side_effect=guard):
            self.assertEqual(trial.score_route(b,Path(tmp),'new_packing',{'minimum_new_chars':900})['status'],'completed')

    def test_incomplete_acquisition_is_preserved_and_not_scored(self):
        p,c=trial.protocol();qid=c['question_ids'][0]
        with TemporaryDirectory() as tmp,patch.dict('os.environ',{'FORECAST_MODEL':p['collection_model'],'FORECAST_MODEL_FALLBACK_SUPER':'1'}), \
             patch.object(trial.pipeline,'run',return_value={'state':{'stage':'collection','interrupted':True}}), \
             patch.object(trial,'score_route') as score:
            out=trial.run_case(Path(tmp),qid)
            self.assertEqual(out['stage'],'acquisition')
            self.assertIn('Acquisition incomplete',out['error'])
            score.assert_not_called()
            self.assertEqual(len(load(Path(tmp)/qid/'executions.json')),1)

    def test_no_missing_or_failed_case_gets_synthetic_metrics(self):
        with TemporaryDirectory() as tmp:
            result=trial.review(Path(tmp))
            self.assertEqual(len(result['rows']),10)
            self.assertFalse(result['complete'])
            self.assertEqual(result['metrics']['first']['scored_pairs'],0)
            self.assertIsNone(result['metrics']['first']['routes']['old_packing']['clipped_brier'])
