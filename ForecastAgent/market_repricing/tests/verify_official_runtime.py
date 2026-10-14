"""Production three-arm wiring, isolation and resume gates; no network requests."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.market_repricing.common import digest, now, policy, read, write
from ForecastAgent.market_repricing.runtime import activate


class OfficialRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        activate(os.environ['MARKET_ENGINE_DIRECTORY'])

    def inputs(self):
        from ForecastAgent.tests.test_research_map_paired import inputs, mapped
        from ForecastAgent.market_repricing.tests.verify_development_runtime import DevelopmentRuntime
        from ForecastAgent.market_repricing.forecast import prepare
        _, common, _, _, child, _ = inputs()
        child = mapped(child, common)
        anchor = {'status': 'usable', 'received_at': now(), 'midpoint': .45}
        market = DevelopmentRuntime().bundle()[0] | {'created_at': anchor['received_at']}
        cfg = policy() | {'analysis_arms': ['original', 'mapped', 'official_map']}
        states, heads, audit = prepare(child, anchor, cfg, market)
        return child, market, anchor, cfg, states, heads, audit

    @staticmethod
    def response(state, folder, heads, **kwargs):
        return {'model': 'inception/mercury-decide:free', 'answers': {
            'event_yes': {'type': 'noul', 'noul': .5},
            **{k: {'type': 'choice', 'choice': 'p10', 'confidence': 1,
                'probabilities': {v: 1 if v == 'p10' else 0 for v in heads[k]['criteria']}}
                for k in heads if k.startswith('price_')}}}

    @staticmethod
    def graph(common, *args, **kwargs):
        from ForecastAgent.market_repricing.official_map_references import replay_reference
        goal = common['question'].get('title') or common['question']['question']
        proposal = {'nodes': [
            {'title': goal, 'category': 'goal', 'desc': 'Immutable target', 'reference_ids': []},
            {'title': 'Reported context', 'category': 'milestone', 'desc': 'Context may inform the target.',
             'reference_ids': [common['evidence'][0]['evidence_id']]}],
            'edges': [{'source': 'Reported context', 'target': goal, 'type': 'causal', 'confidence': .5}]}
        return replay_reference(common, proposal)

    def test_all_three_share_originals_heads_and_cached_results(self):
        from ForecastAgent.market_repricing.forecast import run
        from ForecastAgent.research_loop import decision_http
        child, market, anchor, cfg, _, _, _ = self.inputs()
        seen = []
        def score(state, folder, heads, **kwargs):
            seen.append((copy.deepcopy(state), copy.deepcopy(heads)))
            return self.response(state, folder, heads, **kwargs)
        with tempfile.TemporaryDirectory() as root, patch.object(decision_http, 'call', side_effect=score), patch(
                'ForecastAgent.market_repricing.official_map_references.construct_reference', side_effect=self.graph) as builder:
            result = run(child, market, anchor, root, cfg)
            self.assertEqual(run(child, market, anchor, root, cfg), result)
            builder.assert_called_once()
            self.assertEqual(len(seen), 3)
            self.assertEqual(set(result['arms']), {'original', 'mapped', 'official_map'})
            self.assertTrue(all(a['status'] == 'completed' and a['timely'] for a in result['arms'].values()))
            for state, heads in seen[1:]:
                for key, value in seen[0][0].items():
                    self.assertEqual(state[key], value)
                self.assertEqual(heads, seen[0][1])
            self.assertEqual(result['pair_audit']['official_map']['claims_verified'], False)
            self.assertTrue(read(Path(root)/'official_map/state.json')['research_map']['nodes'])
            # Resume after final aggregation was interrupted adopts each sealed arm.
            (Path(root)/'result.json').unlink()
            resumed = run(child, market, anchor, root, cfg)
            self.assertEqual(resumed['arms'], result['arms'])
            self.assertEqual(len(seen), 3)
            builder.assert_called_once()

    def test_map_failure_preserves_valid_controls_and_no_duplicate_on_resume(self):
        from ForecastAgent.market_repricing.forecast import run
        from ForecastAgent.research_loop import decision_http
        child, market, anchor, cfg, _, _, _ = self.inputs()
        with tempfile.TemporaryDirectory() as root, patch.object(decision_http, 'call', side_effect=self.response) as scorer, patch(
                'ForecastAgent.market_repricing.official_map_references.construct_reference', return_value={'status': 'failed'}) as builder:
            result = run(child, market, anchor, root, cfg)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['arms']['official_map']['status'], 'not_comparable')
            self.assertEqual(scorer.call_count, 2)
            (Path(root)/'result.json').unlink()
            run(child, market, anchor, root, cfg)
            self.assertEqual(scorer.call_count, 2)
            builder.assert_called_once()

    def test_official_mercury_failure_preserves_map_and_two_scores(self):
        from ForecastAgent.market_repricing.forecast import run
        from ForecastAgent.research_loop import decision_http
        child, market, anchor, cfg, _, _, _ = self.inputs()
        def score(state, folder, heads, **kwargs):
            if folder.parent.name == 'official_map': raise RuntimeError('Fixture provider failure')
            return self.response(state, folder, heads, **kwargs)
        with tempfile.TemporaryDirectory() as root, patch.object(decision_http, 'call', side_effect=score) as scorer, patch(
                'ForecastAgent.market_repricing.official_map_references.construct_reference', side_effect=self.graph):
            result = run(child, market, anchor, root, cfg)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['arms']['official_map']['status'], 'failed')
            self.assertTrue((Path(root)/'official_map/state.json').exists())
            (Path(root)/'result.json').unlink()
            run(child, market, anchor, root, cfg)
            self.assertEqual(scorer.call_count, 3)

    def test_slow_optional_arm_keeps_earlier_seal_and_late_arm_ineligible(self):
        from datetime import timedelta
        from ForecastAgent.market_repricing.common import instant
        from ForecastAgent.market_repricing.forecast import run
        from ForecastAgent.research_loop import decision_http
        child, market, anchor, cfg, _, _, _ = self.inputs()
        clock = {'value': anchor['received_at']}
        def score(state, folder, heads, **kwargs):
            if folder.parent.name == 'official_map':
                clock['value'] = (instant(anchor['received_at']) + timedelta(minutes=31)).isoformat()
            return self.response(state, folder, heads, **kwargs)
        with tempfile.TemporaryDirectory() as root, patch.object(decision_http, 'call', side_effect=score), patch(
                'ForecastAgent.market_repricing.official_map_references.construct_reference', side_effect=self.graph), patch(
                'ForecastAgent.market_repricing.forecast.now', side_effect=lambda: clock['value']):
            result = run(child, market, anchor, root, cfg)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['sealed_at'], anchor['received_at'])
            self.assertFalse(result['arms']['official_map']['timely'])
            self.assertTrue(result['arms']['original']['timely'])

    def test_legacy_task_keeps_two_arms_new_release_freezes_three(self):
        from ForecastAgent.market_repricing.worker import forecast_policy, run
        from ForecastAgent.market_repricing.common import seal, state
        from ForecastAgent.market_repricing.deployment import task_release
        from ForecastAgent.market_repricing.tests.test_repricing import normalize, raw_poly
        cfg = policy()
        self.assertEqual(forecast_policy({}, cfg)['analysis_arms'], ['original', 'mapped'])
        market = normalize('polymarket', raw_poly())
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'MARKET_PIPELINE_SHA': '1'*40}):
            root = Path(root); monitor = root/'monitor'; worker = root/'worker'
            source = state(monitor, 'monitor', cfg); source['last_attempt_at'] = now()
            source['tasks'][market['key']] = {'market': market, 'status': 'queued', **task_release()}
            write(monitor/'state.json', source); seal(monitor)
            run(worker, monitor, verification_only=True)
            saved = read(worker/'state.json')['tasks'][market['key']]
            self.assertEqual(forecast_policy(saved, cfg)['analysis_arms'], ['original', 'mapped', 'official_map'])
            self.assertEqual(saved['executions'], 0)
            from ForecastAgent.market_repricing.search_policy import identity
            self.assertEqual(saved['market_search_profile'], identity())

    def test_native_search_inherits_tavily_routing_and_one_logical_search(self):
        import io
        import json
        import sys
        import urllib.request
        from urllib.error import HTTPError
        from ForecastAgent.market_repricing.worker import provider_budget
        from ForecastAgent.market_repricing.tests.test_search_credentials import Response, ENV
        # Exercise the pinned provider imported after transport installation.
        sys.modules.pop('ForecastAgent.providers.tavily_search', None)
        sent=[]
        def send(request, **kwargs):
            sent.append(request)
            if len(sent)==1:
                raise HTTPError(request.full_url,432,'Plan limit',{},io.BytesIO(b'{}'))
            return Response(json.dumps({'results':[],'usage':{'credits':1}}).encode())
        ledger={'provider_reservations':[]}
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,ENV),patch('urllib.request.urlopen',side_effect=send):
            with provider_budget(root,ledger,'native-task',policy()):
                from ForecastAgent.providers.tavily_search import search_batch
                result=search_batch('Federal Reserve December decision',ENV['TAVILY_API_KEY'])
        self.assertEqual(result['usage']['credits'],1)
        self.assertEqual(len(ledger['provider_reservations']),2)
        self.assertEqual(sent[0].data,sent[1].data)
        self.assertEqual(json.loads(sent[1].data)['search_depth'],'basic')
        self.assertEqual(sent[1].get_header('Authorization'),'Bearer '+ENV['TAVILY_API_KEY2'])


if __name__ == '__main__': unittest.main()
