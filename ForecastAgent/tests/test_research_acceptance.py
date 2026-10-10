"""Partial maps, narrative quarantine, immutable caps and bound outcomes."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop import state, decision, labels
from ForecastAgent.research_loop.acceptance import accept, MapAcceptanceError
from ForecastAgent.research_loop.evaluate_trial import score
from ForecastAgent.research_loop.live_trial import parse_map, visible_catalog
from ForecastAgent.tests.test_research_loop import source_bundle, proposal
from ForecastAgent.tests.test_research_live_trial import message


class NodeAcceptanceTests(unittest.TestCase):
    def test_bad_node_isolated_and_never_repeated_in_paths(self):
        b = source_bundle(); p = proposal(b)
        p['nodes'][1].update(evidence_ids=['missing'], claim='QUARANTINED CLAIM')
        p['supporting_path'] = 'QUARANTINED CLAIM'
        raw = copy.deepcopy({k: b[k] for k in ('pages', 'searches', 'fetch_attempts')})
        result = accept(b, p)
        self.assertEqual(result['acceptance']['accepted_node_ids'], ['observed_report', 'unpublished'])
        self.assertEqual(len(result['acceptance']['rejected']), 2)
        self.assertNotIn('QUARANTINED CLAIM', json.dumps(decision.prepare(b)['enriched']))
        self.assertEqual(raw, {k: b[k] for k in raw})
        self.assertTrue(accept(b, p)['cached'])
        self.assertEqual(b['research_loop']['revision'], 1)
        state.verify_journal(b['research_loop'])

    def test_each_semantic_or_shape_failure_is_quarantined(self):
        for change in ({'gap_reason': 'not_found'}, {'evidence_ids': []},
                       {'evidence_ids': ['missing']}, {'event_time': 'invented', 'time_status': 'source_stated'},
                       {'extra': .8}, {'id': 'unsafe id'}, {'evidence_ids': ['R1'] * 4}):
            b = source_bundle(); p = proposal(b)
            second = copy.deepcopy(p['nodes'][0]); second.update(id='good')
            p['nodes'].append(second); p['nodes'][0].update(change)
            result = accept(b, p)
            self.assertIn('good', result['acceptance']['accepted_node_ids'])
            self.assertTrue(result['acceptance']['rejected'])
            self.assertNotIn(p['nodes'][0]['id'], result['acceptance']['accepted_node_ids'])

    def test_all_duplicates_removed_without_guessing(self):
        b = source_bundle(); p = proposal(b)
        p['nodes'].append(copy.deepcopy(p['nodes'][1]))
        result = accept(b, p)
        self.assertNotIn('demand', result['acceptance']['accepted_node_ids'])
        self.assertEqual(sum(r['section'] == 'nodes' for r in result['acceptance']['rejected']), 2)

    def test_envelope_failure_and_no_grounded_node_are_atomic(self):
        for mutate in (lambda p: p.update(probability=.9), lambda p: p.update(expected_revision=1),
                       lambda p: p.update(material_sha256='bad'),
                       lambda p: p['nodes'][0].update(gap_reason='not_found')):
            b = source_bundle(); before = copy.deepcopy(b); p = proposal(b); mutate(p)
            with self.assertRaises(ValueError): accept(b, p)
            self.assertEqual(before, b)

    def test_common_coverage_filters_nodes_not_entire_map(self):
        b = source_bundle(); common = decision.prepare(b)['baseline']; _, refs = visible_catalog(b, common)
        p = proposal(b); p['nodes'][1]['evidence_ids'] = ['outside']
        _, child = parse_map(message(p), b, [r['evidence_id'] for r in refs])
        self.assertEqual(len(child['research_loop']['current']['nodes']), 2)
        self.assertEqual(decision.prepare(child)['baseline'], common)

    def test_one_stale_node_keeps_other_grounded_observation(self):
        b = source_bundle(); p = proposal(b)
        other = 'https://example.net/other'
        b['pages'][other] = {'content': 'A second independent saved observation. ' * 24}
        material = state.catalog(b); p['material_sha256'] = material['material_sha256']
        ref = next(s['evidence_id'] for s in material['spans'].values() if s['url'] == other)
        p['nodes'].append({**p['nodes'][0], 'id': 'other', 'evidence_ids': [ref]})
        state.update(b, p)
        b['pages']['https://example.org/report']['content'] += ' Changed.'
        report = state.audit(b)
        self.assertTrue(report['usable']); self.assertEqual(report['status'], 'partial_invalid_bindings')
        enriched = decision.prepare(b)['enriched']['research_map']
        self.assertEqual([n['id'] for n in enriched['nodes'] if n['kind'] == 'observation'], ['other'])

    def test_size_limit_packs_whole_nodes_and_keeps_raw_text(self):
        b = source_bundle(); p = proposal(b); state.update(b, p)
        before = decision.prepare(b)['baseline']
        with patch.object(decision, 'MAP_BYTE_CAP', 1500):
            after = decision.prepare(b)
        self.assertEqual(before, after['baseline'])
        self.assertEqual(after['baseline'], {k: after['enriched'][k] for k in after['baseline']})
        self.assertLessEqual(len(json.dumps(after['enriched']['research_map']).encode()), 1500)

    def test_corrections_do_not_reset_lifetime_update_cap(self):
        b = source_bundle(); accept(b, proposal(b))
        for revision in (1, 2):
            p = proposal(b, revision, True); p['alternative_path'] = 'Correction '+str(revision)
            accept(b, p)
        p = proposal(b, 3, True); p['alternative_path'] = 'Final correction'
        with self.assertRaisesRegex(ValueError, 'cap exhausted'): accept(b, p)


class LabelBindingTests(unittest.TestCase):
    def binding(self, root, kind='binary'):
        q = source_bundle(kind)['request']; q['unit'] = 'USD' if kind == 'numeric' else ''
        label = {'type': kind, 'value': 1 if kind == 'binary' else 100,
                 'result_kind': 'numeric'}
        if kind == 'multiple_choice': label['resolution_display'] = q['options'][0]
        ref = {'question': labels.contract(q), 'outcome': copy.deepcopy(label)}
        path = Path(root)/'reference.json'; path.write_text(json.dumps(ref), encoding='utf-8')
        prov = {'source_path': str(path), 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'source_url': 'https://www.metaculus.com/questions/'+str(q['id']),
            'observed_at': '2026-10-01T00:00:00Z', 'method': 'archived_result_record'}
        return q, labels.bind(q, label, ref, prov)

    def test_bound_label_and_matching_metric_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            for kind in ('binary', 'numeric', 'multiple_choice'):
                q, binding = self.binding(tmp, kind)
                self.assertTrue(labels.validate(q, binding)['eligible'])

    def test_id_only_labels_not_scoreable(self):
        self.assertFalse(labels.validate(source_bundle()['request'], {'value': 1})['eligible'])

    def test_identity_units_period_rules_and_options_are_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            for kind, field, value in [('binary', 'question', 'Different event'),
                                      ('binary', 'resolution_criteria', 'Different time'),
                                      ('binary', 'id', 'different'),
                                      ('binary', 'question_type', 'date'),
                                      ('numeric', 'unit', 'percent'),
                                      ('multiple_choice', 'options', ['different', 'options'])]:
                q, binding = self.binding(tmp, kind); q[field] = value
                self.assertFalse(labels.validate(q, binding)['eligible'])

    def test_label_disagreement_and_independent_metric_conflict_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, binding = self.binding(tmp)
            wrong = copy.deepcopy(binding); wrong['label']['value'] = 0
            wrong['binding_sha256'] = digest({k:v for k,v in wrong.items() if k != 'binding_sha256'})
            self.assertFalse(labels.validate(q, wrong)['eligible'])
            binding['conflicts'] = [{'issue': 'Permits result used for housing starts'}]
            binding['binding_sha256'] = digest({k:v for k,v in binding.items() if k != 'binding_sha256'})
            self.assertEqual(labels.validate(q, binding)['status'], 'quarantined')

    def test_archived_reference_mutation_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, binding = self.binding(tmp)
            Path(binding['provenance']['source_path']).write_text('changed', encoding='utf-8')
            self.assertFalse(labels.validate(q, binding)['eligible'])

    def test_date_label_uses_utc_timestamp_not_float_cast(self):
        q = source_bundle('date')['request']
        from ForecastAgent.analysis.distributions import grid, range_metadata
        xs = grid(range_metadata(q))
        from datetime import datetime, timezone
        label = {'result_kind': 'date', 'value': datetime.fromtimestamp((xs[0]+xs[-1])/2, timezone.utc).isoformat()}
        result = score({'continuous_cdf': [i/(len(xs)-1) for i in range(len(xs))]}, q, label)
        self.assertAlmostEqual(result['normalized_bounded_cdf_loss'], 1/12)


if __name__ == '__main__':
    unittest.main()
