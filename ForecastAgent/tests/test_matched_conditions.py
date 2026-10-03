"""Coverage equality and pre-inference provenance validation for matched experiments."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis import matched_conditions as mc
from ForecastAgent.tests.test_analysis_p0 import bundle
from ForecastAgent.tests.test_condition_chain import response


def request(b):
    packet=mc.chain.full_packet(b)
    state,_=mc.chain.select(packet)
    return {'state':state,'questions':mc.chain.questions(),'model':mc.decisions.MODEL}


class MatchedTests(unittest.TestCase):
    def test_exact_originals_and_condition_receipts_in_both_stages(self):
        b=bundle();frozen=request(b);before=copy.deepcopy(frozen)
        def call(state,folder,questions):
            self.assertEqual(state['evidence'],frozen['state']['evidence'])
            self.assertEqual(state['sources'],frozen['state']['sources'])
            self.assertEqual(state['question'],frozen['state']['question'])
            if 'event_yes' in questions:
                self.assertTrue(state['condition_bindings']['receipts'])
                return {'answers':{'event_yes':{'noul':.4}}}
            return response(questions)
        with tempfile.TemporaryDirectory() as root,patch.object(mc.chain,'call',side_effect=call) as mock:
            result=mc.run_task(b,Path(root),frozen)
            self.assertEqual(result['coverage']['evidence_removed'],0)
            self.assertEqual(mock.call_count,2)
        self.assertEqual(frozen,before)

    def test_changed_original_body_fails_before_network(self):
        b=bundle();frozen=request(b);span=frozen['state']['evidence'][0]
        b['pages'][span['url']]['content']='tampered'
        with tempfile.TemporaryDirectory() as root,patch.object(mc.chain,'call') as mock:
            with self.assertRaises(ValueError):mc.run_task(b,Path(root),frozen)
            mock.assert_not_called()

    def test_exceeded_bound_never_trims_evidence(self):
        b=bundle();frozen=request(b);before=copy.deepcopy(frozen)
        with tempfile.TemporaryDirectory() as root,patch.object(mc,'REQUEST_BYTES',1),patch.object(mc.chain,'call') as mock:
            with self.assertRaisesRegex(ValueError,'no text removed'):mc.run_task(b,Path(root),frozen)
            mock.assert_not_called()
        self.assertEqual(frozen,before)


if __name__=='__main__':unittest.main()
