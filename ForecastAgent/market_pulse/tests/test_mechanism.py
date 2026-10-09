"""Generic acquisition routing, immutable evidence and bounded recovery."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from io import BytesIO

from ForecastAgent.market_pulse import mechanism as m, mechanism_trial as t
from ForecastAgent.market_pulse.tests.test_source_coverage import bundle
from ForecastAgent.analysis.pilot import save, load, digest
from ForecastAgent.market_pulse import mechanism_acquisition as integration


def response(action, driver):
    return {'tool_calls': [{'function': {'name': 'choose_research_action', 'arguments': json.dumps({
        'action_id': action, 'driver': driver, 'reason': 'Read original issuer context.', 'remaining_gaps': ['Period interpretation remains unverified.']})}}]}


def chooser(messages, key, **kwargs):
    observer = kwargs['observer']; record = {'status': 'reserved'}
    token = observer('reserve', record)
    prompt = json.loads(messages[-1]['content']); action = prompt['available_actions'][0]
    message = response(action['action_id'], action['driver'])
    record.update(status='received', response={'choices': [{'message': message}], 'usage': {'total_tokens': 10}})
    observer('complete', record, token)
    return message


class MechanismTests(unittest.TestCase):
    def test_unseen_eps_issuer_uses_mechanisms(self):
        b = bundle('Delta', extra='Effective tax rate and diluted shares affected operating margin.')
        original = copy.deepcopy(b); p = m.plan(b, [])
        self.assertEqual(p['contract']['family'], 'diluted_eps')
        self.assertTrue({'tax', 'shares', 'margin'} <= {a['driver'] for a in p['actions']})
        self.assertEqual(b, original)

    def test_revenue_has_volume_price_fx(self):
        b = bundle('Delta'); b['request']['question'] = 'What will revenue be? (Delta)'
        drivers = {d['id'] for d in m.contract(b['request'])['drivers']}
        self.assertTrue({'volume', 'price_mix', 'currency', 'segments'} <= drivers)
        self.assertNotIn('tax', drivers)

    def test_guidance_contract_keeps_forward_quarter(self):
        b = bundle(); b['request'].update(question='What will Zeta guidance be in Q3 FY2028? (Revenue)',
            unit='Billion $', resolution_criteria='The midpoint of the guidance range for Q4 FY2028 in Zeta Q3 FY2028 earnings press release, rounded to nearest billion.')
        c = m.contract(b['request'])
        self.assertEqual(c['family'], 'guidance'); self.assertEqual(c['target_fiscal_period'], [4, 2028])
        self.assertFalse(c['future_actual_required'])

    def test_guidance_leaf_metric_is_not_issuer(self):
        b = bundle('Delta'); b['request'].pop('issuer_label')
        b['request'].update(question="What will be Delta's forward guidance in its Q3 FY2028 earnings release? (Revenue)",
            unit='Billion $', resolution_criteria='The midpoint of the guidance range for Q4 FY2028 in Delta Q3 FY2028 earnings press release, rounded to nearest billion.')
        self.assertEqual(m.plan(b, [])['contract']['issuer'], 'Delta')
        prepared = integration.prepare(b['request'])
        self.assertEqual(prepared['financial_acquisition_policy']['issuer']['issuer_label'], 'Delta')
        self.assertEqual(prepared['financial_issuer_original_ref']['quote'], 'Delta')
        self.assertEqual(prepared['question'], b['request']['question'])

    def test_conflicting_guidance_issuer_is_not_silently_changed(self):
        b = bundle('Delta'); b['request'].update(question="What will be Other's forward guidance in its Q3 FY2028 earnings release? (Revenue)",
            unit='Billion $', resolution_criteria='The midpoint of the guidance range for Q4 FY2028 in Other Q3 FY2028 earnings press release, rounded to nearest billion.')
        with self.assertRaises(ValueError): m.plan(b, [])

    def test_no_synthesized_url_and_search_handoff_only(self):
        b = bundle(); p = m.plan(b, [])
        self.assertFalse(any(a['tool'] == 'fetch_public' for a in p['actions']))
        self.assertTrue(all(s['must_use_original_acquisition_ledger'] for s in p['search_handoffs']))
        self.assertEqual(p['search_lifetime_max'], {'tavily_basic': 3, 'exa': 1})

    def test_observed_link_can_be_candidate(self):
        b = bundle(); page = next(iter(b['pages'].values()))
        page['links'] = [{'href': '/financial-results-q4', 'text': 'Financial results'}, {'href': 'http://user:secret@example.org/results'}]
        urls = m.observed_urls(b)
        self.assertIn('https://www.zeta.com/financial-results-q4', urls)
        self.assertFalse(any('secret' in u for u in urls))

    def test_unknown_ids_and_driver_mismatch_rejected(self):
        p = m.plan(bundle(extra='tax rate'), []); action = p['actions'][0]
        with self.assertRaises(ValueError): m.choose(response('invented', action['driver']), p['actions'], {'tax'})
        with self.assertRaises(ValueError): m.choose(response(action['action_id'], 'volume'), p['actions'], {'tax'})

    def test_forecast_fields_do_not_enter(self):
        b = bundle(); b['request']['resolved_to'] = True
        with self.assertRaises(ValueError): m.plan(b, [])

    def test_catalog_balances_drivers(self):
        rows = [{'driver': 'tax'} for _ in range(40)] + [{'driver': 'margin'}]
        self.assertEqual(t.balanced(rows, 2)[1]['driver'], 'margin')

    def test_retrospective_expectations_are_not_guidance(self):
        b = bundle(extra='Revenue was above our expectations in the reported quarter.')
        self.assertFalse(any(a['driver'] == 'guidance' for a in m.plan(b, [])['actions']))

    def test_derived_views_deduplicate_same_passage(self):
        b = bundle(extra='Guidance for next quarter remains unknown.')
        page = next(iter(b['pages'].values())); text = page['content']
        page['documents'] = [{'page_content': text}, {'page_content': text + '\nDifferent appendix.'}]
        p = m.plan(b, [])
        guides = [a for a in p['actions'] if a['driver'] == 'guidance']
        self.assertEqual(len(guides), 1)
        self.assertEqual(len(guides[0]['duplicate_coordinate_views']), 2)

    def test_original_prior_period_is_context_not_target_guidance(self):
        scope = m.contract(bundle()['request'])
        hint = m.routing_hint('https://example.org/earnings', 'Q1 FY2025\nGuidance and outlook.', scope, [])
        self.assertEqual(hint['source_role'], 'prior_report_context_candidate')
        self.assertFalse(hint['guidance_applies_to_target_verified'])

    def test_continuation_starts_after_model_visible_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); b = bundle(extra='tax rate ' + 'x'*21000)
            action = next(a for a in m.plan(b, [])['actions'] if a['driver'] == 'tax')
            folder = root/'materials'/action['action_id']
            receipt = t.execute(action, b, folder)
            entry = {**receipt, 'action_id': action['action_id'], 'driver': 'tax', 'tool': 'read_saved'}
            working, packets = t.current_materials(b, {'actions': [entry]}, root)
            follow = t.continuations(working, packets)[0]
            self.assertEqual(follow['args']['start_char'], action['args']['start_char']+9000)
            self.assertEqual(follow['args']['document_index'], action['args']['document_index'])
            self.assertEqual(packets[0]['model_omitted_chars'], 0)
            packet = load(folder/'reading-packet.json'); packet['content'] = 'tampered'
            save(folder/'reading-packet.json', packet)
            with self.assertRaises(ValueError): t.current_materials(b, {'actions': [entry]}, root)

    def test_same_failed_url_is_not_retried(self):
        action = {'action_id': 'one', 'driver': 'tax', 'tool': 'fetch_public', 'args': {'url': 'https://example.org/results'}}
        journal = {'actions': [{'action_id': 'different', 'tool': 'fetch_public', 'url': action['args']['url'], 'status': 'http_error'}]}
        self.assertFalse(t.available({'actions': [action]}, journal, t.DEFAULT_LIMITS, True))

    def test_http_failure_preserves_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            def failed(*args, **kwargs): raise HTTPError(args[0], 403, 'Forbidden', {}, BytesIO(b'blocked'))
            result = t.execute({'tool': 'fetch_public', 'args': {'url': 'https://example.org/results'}}, bundle(), Path(tmp), fetcher=failed)
            self.assertEqual(result['status'], 'http_error')
            self.assertEqual((Path(tmp)/'http-error.bin').read_bytes(), b'blocked')

    def test_resume_caps_and_parent_immutability(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'FORECAST_MODEL': 'nvidia/nemotron-3-super-120b-a12b:free'}):
            root = Path(tmp); p = root/'bundle.json'; v = root/'variables.json'
            save(p, bundle(extra='Effective tax rate and diluted shares influenced margins.'))
            save(v, []); original = p.read_bytes()
            limits = {'model_http': 1, 'local_reads': 4, 'free_captures': 0}
            a = t.run(p, v, root/'stage', limits=limits, ask=chooser)
            b = t.run(p, v, root/'stage', limits=limits, ask=chooser)
            self.assertEqual(a['model_http_attempts'], 1); self.assertEqual(b['model_http_attempts'], 1)
            self.assertEqual(len(a['actions']), len(b['actions']))
            self.assertEqual(p.read_bytes(), original)
            self.assertEqual(a['tavily_calls'], 0); self.assertFalse(a['submitted'])

    def test_interrupted_reservation_is_preserved(self):
        journal = {'actions': [{'action_id': 'x', 'tool': 'fetch_public', 'url': 'https://example.org/results', 'status': 'reserved'}]}
        self.assertFalse(t.available({'actions': [{'action_id': 'x', 'tool': 'fetch_public', 'driver': 'tax', 'args': {'url': 'https://example.org/results'}}]}, journal, t.DEFAULT_LIMITS, True))

    def test_frozen_budget_cannot_be_enlarged_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'FORECAST_MODEL': 'nvidia/nemotron-3-super-120b-a12b:free'}):
            root = Path(tmp); p = root/'bundle.json'; v = root/'variables.json'
            save(p, bundle()); save(v, [])
            t.run(p, v, root/'stage', limits={'model_http': 0, 'local_reads': 0, 'free_captures': 0})
            with self.assertRaises(ValueError): t.run(p, v, root/'stage', limits=t.DEFAULT_LIMITS)

    def test_original_collector_search_cap_and_restore(self):
        from types import SimpleNamespace
        from ForecastAgent.runtime import retrieval
        original = retrieval.RetrievalTask.execute
        request = integration.prepare(bundle()['request'])
        task = SimpleNamespace(bundle={'request': request, 'searches': [{}, {}, {}], 'exa_searches': [{}]})
        with integration.collector_policy():
            with self.assertRaisesRegex(ValueError, 'lifetime search'): retrieval.RetrievalTask.execute(task, 'search_tavily', {}, '')
            with self.assertRaisesRegex(ValueError, 'lifetime search'): retrieval.RetrievalTask.execute(task, 'search_exa', {}, '')
        self.assertIs(retrieval.RetrievalTask.execute, original)

    def test_policy_migration_is_explicit(self):
        request = integration.prepare(bundle()['request'])
        request['financial_mechanism_policy']['search_lifetime_max']['tavily_basic'] = 4
        with self.assertRaises(ValueError): integration.prepare(request)

    def test_new_capture_can_extend_frontier_without_parent_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); b = bundle(); original = copy.deepcopy(b)
            folder = root/'materials'/'capture'; folder.mkdir(parents=True)
            page = {'content': 'saved material', 'links': [{'url': 'https://www.zeta.com/earnings/details', 'text': 'earnings'}]}
            save(folder/'capture.json', page)
            journal = {'actions': [{'action_id': 'capture', 'status': 'captured_candidate',
                'url': 'https://www.zeta.com/new-results', 'capture_json_sha256': digest(page)}]}
            working, _ = t.current_materials(b, journal, root)
            self.assertIn('https://www.zeta.com/earnings/details', m.observed_urls(working))
            self.assertEqual(b, original)


if __name__ == '__main__': unittest.main()
