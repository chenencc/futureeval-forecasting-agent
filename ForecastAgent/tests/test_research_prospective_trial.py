"""Common-original pairing, conservative map delivery and immutable retries."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.research_loop import prospective_trial as trial, full_loop_trial
from ForecastAgent.research_loop import delta, delivery, reading_views, gap_feedback, simple_map
from ForecastAgent.research_loop.acceptance import accept
from ForecastAgent.tests.test_research_loop import source_bundle
from ForecastAgent.tests.test_research_simple_map import literal_proposal


class ProspectiveTests(unittest.TestCase):
    def test_common_originals_and_same_heads_with_real_bound_map(self):
        bundle=source_bundle(); accept(bundle,literal_proposal(bundle),map_protocol=simple_map.PROTOCOL)
        before=copy.deepcopy(bundle)
        common,pair,audit=trial.prepared_pair(bundle)
        self.assertEqual(bundle,before)
        self.assertTrue(audit['map_delivered'])
        self.assertNotIn('research_map',pair['original']['state'])
        self.assertEqual(pair['original']['questions'],pair['mapped']['questions'])
        for key,value in common.items():
            self.assertEqual(pair['mapped']['state'][key],value)
        self.assertEqual(set(pair['original']['questions']),{'event_yes'})

    def test_view_quotes_not_visible_in_raw_originals_are_omitted(self):
        common={'question':{'id':'1'},'sources':[{'source_id':'S1','url':'u','body_sha256':'raw'}],
                'evidence':[{'source_id':'S1','text':'Raw text only.'}]}
        mapped={'factual_grounding_present':True,'nodes':[{'id':'n1','kind':'observation',
                'claim':'Decoded text','bindings':[{'url':'u','body_sha256':'raw','view_sha256':'view'}]}],
                'relations':[],'material_requests':[]}
        def prepare(c,h,s,child=None):
            result={'state':copy.deepcopy(c),'questions':h}
            if child is not None:result['state']['research_map']=copy.deepcopy(mapped)
            return result
        with patch.object(trial,'registry',return_value=({'event_yes':{}},None)), \
                patch.object(trial,'pack',return_value=(common,{})), \
                patch.object(trial.map_paired_trial,'prepare',side_effect=prepare):
            _,pair,audit=trial.prepared_pair({'request':{}})
        self.assertFalse(audit['map_delivered'])
        self.assertEqual(audit['literal_observations_omitted_from_scoring'],['n1'])
        self.assertEqual(pair['original']['state'],pair['mapped']['state'])

    def test_freeze_rejects_input_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'frozen.json'
            trial.freeze(path,{'text':'original'})
            trial.freeze(path,{'text':'original'})
            with self.assertRaisesRegex(ValueError,'Frozen scoring input changed'):
                trial.freeze(path,{'text':'modified'})

    def test_latest_feedback_policies_are_explicit_not_default(self):
        bundle=source_bundle()
        plain=full_loop_trial.input_for(bundle,'2026-10-09T00:00:00+00:00')
        current=full_loop_trial.input_for(bundle,'2026-10-09T00:00:00+00:00',gap_feedback=True)
        for module in (delta,delivery,reading_views,gap_feedback):
            self.assertNotIn(module.FIELD,plain)
            self.assertEqual(current[module.FIELD],module.POLICY)
        self.assertNotIn('pages',current)
        self.assertNotIn('resolution',current)


if __name__=='__main__':
    unittest.main()
