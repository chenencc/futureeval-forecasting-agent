"""Reference-selection interface, immutable originals and bounded cached replay."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.research_loop import decision, live_trial, reference_map, target_logic
from ForecastAgent.research_loop import release_paired_trial as release_pair
from ForecastAgent.research_loop.acceptance import MapAcceptanceError
from ForecastAgent.runtime.records import load, save
from ForecastAgent.tests.test_research_reference_map import proposal
from ForecastAgent.tests.test_research_target_logic import fixture
from ForecastAgent.tests.test_research_live_trial import message


class ReleaseReferenceTests(unittest.TestCase):
    def inputs(self):
        child, _ = fixture()
        proposed = proposal(child)
        common = decision.prepare(child)['baseline']
        return child, proposed, common

    def test_reference_prompt_and_schema_bind_complete_originals_then_cache(self):
        child, proposed, common = self.inputs()
        untouched = copy.deepcopy(child)
        calls = []
        def fake(messages, key, **kwargs):
            calls.append((messages, kwargs))
            token = kwargs['observer']('reserve', {'status': 'reserved', 'request': {'model': live_trial.SUPER}})
            kwargs['observer']('complete', {'status': 'received'}, token)
            return message(proposed)
        with tempfile.TemporaryDirectory() as tmp, patch.object(live_trial, 'ask_model', side_effect=fake):
            first = live_trial.map_stage(child, common, tmp, 'offline', http_cap=1,
                map_protocol=reference_map.PROTOCOL, frozen_scoring_coverage=True)
            again = live_trial.map_stage(child, common, tmp, 'offline', http_cap=1,
                map_protocol=reference_map.PROTOCOL, frozen_scoring_coverage=True)
            self.assertEqual(first, again)
            self.assertEqual(len(calls), 1)
            req = load(Path(tmp)/'request.json')
            fields = req['tools'][0]['function']['parameters']['properties']['nodes']['items']['properties']
            self.assertIn('hypothesis', fields)
            self.assertNotIn('claim', fields)
            self.assertNotIn('event_time', fields)
            state = json.loads(req['messages'][1]['content'])
            self.assertEqual(state['targets'], target_logic.targets(child))
            self.assertEqual(state['original_evidence'], live_trial.map_original_view(child, common))
            records = first['research_loop']['events'][-1]['acceptance']['reference_binding_receipts']
            self.assertTrue(records[0]['applied'])
            for node in first['research_loop']['current']['nodes']:
                for binding in node['bindings']:
                    self.assertEqual(child['pages'][binding['url']]['content'][binding['start']:binding['end']], binding['text'])
                    self.assertFalse(binding['meaning_verified'])
        self.assertEqual(child, untouched)

    def test_reference_protocol_without_explicit_policy_never_calls_provider(self):
        child, _, common = self.inputs()
        child['request'].pop(reference_map.FIELD)
        with tempfile.TemporaryDirectory() as tmp, patch.object(live_trial, 'ask_model') as provider:
            with self.assertRaisesRegex(ValueError, 'explicit policy'):
                live_trial.map_stage(child, common, tmp, 'offline', map_protocol=reference_map.PROTOCOL)
            provider.assert_not_called()

    def test_hidden_reference_and_copied_claim_remain_rejected(self):
        for mode in ('hidden', 'copied'):
            child, proposed, common = self.inputs()
            proposed['nodes'] = proposed['nodes'][:1]
            if mode == 'hidden': proposed['nodes'][0]['evidence_ids'] = ['R_invented']
            else: proposed['nodes'][0]['claim'] = 'A fabricated realized outcome.'
            allowed = [r['evidence_id'] for r in live_trial.visible_catalog(child, common)[1]]
            with self.subTest(mode=mode), self.assertRaises(MapAcceptanceError):
                live_trial.parse_map(message(proposed), child, allowed,
                    map_protocol=reference_map.PROTOCOL, scoring_baseline=common)

    def test_release_identity_binds_protocol_and_cannot_change_after_preparation(self):
        from ForecastAgent.tests.test_competition_mercury import bundle
        with tempfile.TemporaryDirectory() as tmp, patch.object(release_pair, 'verify_control', return_value={}):
            parent = Path(tmp)/'parent.json'; folder = Path(tmp)/'case'; save(parent, bundle())
            original = load(parent)
            result = release_pair._run(parent, folder, Path(tmp), map_protocol=reference_map.PROTOCOL)
            self.assertEqual(result['map_protocol'], reference_map.PROTOCOL)
            self.assertEqual(result['new_http'], 0)
            self.assertEqual(load(parent), original)
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                release_pair._run(parent, folder, Path(tmp))
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                release_pair._run(parent, Path(tmp)/'other', Path(tmp), map_protocol='invented')


if __name__ == '__main__':
    unittest.main()
