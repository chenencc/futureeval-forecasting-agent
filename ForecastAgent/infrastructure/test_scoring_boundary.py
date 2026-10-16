"""Same exact rule-preserving input in both hosts and unchanged market states."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

if '--engine' in sys.argv:
    position = sys.argv.index('--engine')
    sys.path.insert(0, str(Path(sys.argv[position+1]).resolve()))
    del sys.argv[position:position+2]
from scoring_boundary import project_bundle, market_adapter, LOCAL_METADATA_FIELDS
from competition_context import project as competition_project
from ForecastAgent.research_loop import scoring_delivery, prospective_trial
from ForecastAgent.intelligence.admission import prepare as admit
from ForecastAgent.tests.test_research_target_logic import fixture
from ForecastAgent.tests.test_research_reference_map import proposal, apply


class BoundaryTests(unittest.TestCase):
    def test_unknown_platform_fields_and_all_originals_are_preserved(self):
        raw = {'request': {'question': 'Exact', 'resolution_criteria': 'Exact rule',
            'unknown_platform_exception': 'Keep this literal exception', 'capability_code_identity': {'x': 'large'},
            'predictive_information_contract': {'x': 'large'}}, 'pages': {'u': {'content': 'literal'}}}
        original = copy.deepcopy(raw)
        self.assertEqual(project_bundle(raw), competition_project(raw))
        clean = project_bundle(raw)
        self.assertEqual(clean['request']['unknown_platform_exception'], 'Keep this literal exception')
        self.assertEqual(clean['pages'], raw['pages']); self.assertEqual(raw, original)

    def test_actual_native_market_pair_states_and_heads_are_unchanged(self):
        bundle, _ = fixture(); apply(bundle, proposal(bundle))
        bundle['request'][scoring_delivery.FIELD] = scoring_delivery.POLICY
        bundle['request']['capability_code_identity'] = {'x': 'a'*12000}
        bundle['request']['predictive_information_contract'] = {'x': 'b'*10000}
        bundle['request']['unknown_platform_exception'] = 'Exact unknown condition retained.'
        view, _ = admit(bundle)
        before = copy.deepcopy(bundle)
        native_common, native_pair, _ = prospective_trial.prepared_pair(bundle, original_view=view)
        with market_adapter():
            common, pair, audit = prospective_trial.prepared_pair(bundle, original_view=view)
        self.assertEqual(common, native_common)
        for arm in ['original','mapped']:
            self.assertEqual(pair[arm]['state'], native_pair[arm]['state'])
            self.assertEqual(pair[arm]['questions'], native_pair[arm]['questions'])
        self.assertEqual(common['question'], competition_project(view)['request'])
        self.assertEqual(bundle, before)
        self.assertTrue(audit['selection']['bound_source_delivery']['shared_input_boundary'])

    def test_changed_native_projection_policy_fails_closed(self):
        with patch.object(scoring_delivery, 'LOCAL_METADATA_FIELDS', ('unexpected',)):
            with self.assertRaisesRegex(ValueError, 'differs'):
                with market_adapter(): pass


if __name__ == '__main__': unittest.main()
