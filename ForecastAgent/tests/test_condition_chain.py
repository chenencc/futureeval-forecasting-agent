"""Offline checks of exact provenance, independent stages and frozen resumes."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis import condition_chain as cc
from ForecastAgent.tests.test_analysis_p0 import bundle


def response(questions):
    return {'model': cc.decisions.MODEL, 'answers': {k: {'type': 'choice', 'choice': 'insufficient',
        'probabilities': {option:(1.0 if option=='insufficient' else 0.0) for option in q['criteria']},
        'confidence': .6} for k,q in questions.items()}}


class ConditionChainTests(unittest.TestCase):
    def test_binding_rejects_modified_body(self):
        b=bundle(); _,s,q,bindings=cc.prepare(b)
        r=cc.bind(s,response(q),q,bindings,b)
        self.assertFalse(r['semantic_truth_verified'])
        self.assertEqual(len(r['receipts']),len(bindings))
        broken=copy.deepcopy(b);broken['pages'][s['evidence'][0]['url']]['content']='changed'
        with self.assertRaises(ValueError):cc.bind(s,response(q),q,bindings,broken)

    def test_second_call_consumes_first_answers_and_original_text(self):
        b=bundle();before=copy.deepcopy(b)
        def call(state,folder,questions):
            if 'condition_bindings' in state:
                self.assertFalse(state['condition_bindings']['semantic_truth_verified'])
                self.assertTrue(state['condition_bindings']['receipts'])
                self.assertEqual(set(questions),{'event_yes'})
                self.assertNotIn('event_yes',state['condition_bindings'])
                return {'answers':{'event_yes':{'type':'noul','noul':.99}}}
            self.assertNotIn('event_yes',questions)
            return response(questions)
        with tempfile.TemporaryDirectory() as root,patch.object(cc.chain,'call',side_effect=call) as mock:
            r=cc.run_task(b,Path(root))
            self.assertEqual(mock.call_count,2);self.assertEqual(r['clipped_probability_yes'],.98)
            self.assertEqual(b,before)
            changed=copy.deepcopy(b);changed['request']['fine_print']+=' changed'
            with self.assertRaisesRegex(ValueError,'Frozen'):cc.run_task(changed,Path(root))

    def test_final_failure_does_not_invent_first_stage_forecast(self):
        b=bundle()
        def call(state,folder,questions):
            if 'condition_bindings' in state:raise RuntimeError('Unavailable')
            return response(questions)
        with tempfile.TemporaryDirectory() as root,patch.object(cc.chain,'call',side_effect=call):
            with self.assertRaisesRegex(RuntimeError,'Unavailable'):cc.run_task(b,Path(root))
            self.assertTrue((Path(root)/'condition-bindings.json').exists())
            self.assertFalse((Path(root)/'result.json').exists())

    def test_preflight_uses_no_provider(self):
        with tempfile.TemporaryDirectory() as root,patch.object(cc.chain,'call') as mock:
            r=cc.run_task(bundle(),Path(root),dry_run=True)
            self.assertEqual(r['status'],'prepared');mock.assert_not_called()


if __name__=='__main__':unittest.main()
