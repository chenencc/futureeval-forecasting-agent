"""Exercise selected development acquisition/maps with zero provider requests."""
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.market_repricing.common import now, policy, digest, read
from ForecastAgent.market_repricing.runtime import activate


class DevelopmentRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        activate(os.environ['MARKET_ENGINE_DIRECTORY'])

    def bundle(self):
        from ForecastAgent.market_repricing.worker import request_for
        from ForecastAgent.market_repricing.venues import normalize
        from ForecastAgent.market_repricing.tests.test_repricing import raw_poly
        market = normalize('polymarket', raw_poly())
        text = ('Federal Reserve meeting notice. December 10, 2026 is the scheduled meeting date. '
                'The target range is currently unchanged. This notice does not announce a rate cut.\n') * 30
        bundle = {'request': request_for(market), 'pages': {'https://example.org/fed': {'content': text,
                  'body_diagnostics': {'usable_text': True}, 'retrieved_at_utc': now()}},
                  'gaps': [{'code': 'future_decision_unknown'}]}
        return market, text, bundle

    def test_native_capture_child_reads_absolute_request_from_engine_cwd(self):
        from ForecastAgent.supplement.transport import capture
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root).resolve() / 'capture'
            script = ('import json,sys; from pathlib import Path; '
                      'request=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")); '
                      'Path(sys.argv[2]).write_text(json.dumps({"request":request,"ok":True,'
                      '"page":{"content":"Fixture child read the saved request; no network request occurred."}}),encoding="utf-8")')
            page = capture('http', 'https://example.org/no-network-fixture', folder,
                _command=[sys.executable, '-c', script, str(folder / 'request.json'), str(folder / 'result.json')])
            self.assertIn('Fixture child', page['content'])
            self.assertEqual(page['supplement_transport_boundary']['status'], 'completed')

    def test_development_common_originals_and_market_heads(self):
        from ForecastAgent.market_repricing.forecast import run
        from ForecastAgent.research_loop import decision_http
        market, text, bundle = self.bundle()
        anchor = {'status': 'usable', 'received_at': now(), 'midpoint': .45, 'yes_bid': .43, 'yes_ask': .47}
        seen = []
        def decision(state, root, questions, **kwargs):
            seen.append(state)
            answers = {'event_yes': {'type': 'noul', 'noul': .5}}
            for key, q in questions.items():
                if key.startswith('price_'):
                    answers[key] = {'type': 'choice', 'choice': 'p12', 'confidence': 1,
                                    'probabilities': {k: 1 if k == 'p12' else 0 for k in q['criteria']}}
            return {'model': 'inception/mercury-decide:free', 'answers': answers}
        with tempfile.TemporaryDirectory() as root:
            with patch.object(decision_http, 'call', side_effect=decision):
                result = run(bundle, market, anchor, Path(root), policy())
                again = run(bundle, market, anchor, Path(root), policy())
            self.assertEqual(len(seen), 1)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['arms']['original']['price_means']['72'], .6)
            self.assertEqual(result, again)
            self.assertFalse(result['pair_audit']['map_delivered'])
            self.assertEqual(result['arms']['mapped']['status'], 'not_comparable')
            self.assertEqual(set(result['arms']), {'original', 'mapped'})
            for span in seen[0]['evidence']:
                self.assertEqual(text[span['start']:span['end']], span['text'])

    def test_integrated_development_acquisition_flags_and_resume(self):
        from ForecastAgent.market_repricing.worker import acquire
        from ForecastAgent.intelligence import pipeline
        from ForecastAgent.intelligence.research_map import enable
        from ForecastAgent.channels.contracts import FIELD, POLICY
        market, text, bundle = self.bundle()
        seen = []
        def collect(request, directory, **kwargs):
            seen.append((request, kwargs))
            from ForecastAgent.market_repricing.common import write
            write(directory/'final-map-package.json', bundle | {'request': request})
            return {'view': bundle, 'report': {'test_fixture': True}}
        with tempfile.TemporaryDirectory() as root:
            task = {'market': market}
            with patch.object(pipeline, 'collect', side_effect=collect):
                result = acquire(task, Path(root))
                again = acquire(task, Path(root))
            self.assertEqual(len(seen), 1)
            self.assertEqual(result, again)
            request, kwargs = seen[0]
            self.assertTrue(kwargs['research_map']); self.assertTrue(kwargs['channel_tools'])
            self.assertTrue(kwargs['recover_data'])
            expected = enable(request, predictive_focus=True, scoring_delivery=True)
            self.assertEqual(request, expected)
            self.assertEqual(task['bundle_sha256'], digest(result))
            self.assertNotIn('release_sha', read(Path(root)/'material-integrity.json'))

    def test_accepted_development_map_is_delivered_on_common_originals(self):
        from ForecastAgent.tests.test_research_map_paired import inputs, mapped
        from ForecastAgent.market_repricing.forecast import prepare
        _, common, heads, spec, child, audit = inputs()
        child = mapped(child, common)
        anchor = {'status': 'usable', 'received_at': now(), 'midpoint': .45, 'yes_bid': .43, 'yes_ask': .47}
        states, registry, result = prepare(child, anchor, policy())
        self.assertTrue(result['map_delivered'])
        self.assertTrue(states['mapped']['research_map']['factual_grounding_present'])
        self.assertEqual(states['original']['evidence'], states['mapped']['evidence'])
        self.assertEqual(states['original']['sources'], states['mapped']['sources'])
        self.assertEqual(set(registry), {'event_yes', 'price_72', 'price_168'})
        from ForecastAgent.market_repricing.forecast import run
        from ForecastAgent.research_loop import decision_http
        market = self.bundle()[0]
        calls = []
        def score(state, folder, heads, **kwargs):
            calls.append(state)
            return {'model': 'inception/mercury-decide:free', 'answers': {
                'event_yes': {'type': 'noul', 'noul': .5},
                **{k: {'type': 'choice', 'choice': 'p10', 'confidence': 1,
                       'probabilities': {v: 1 if v == 'p10' else 0 for v in heads[k]['criteria']}}
                   for k in ('price_72', 'price_168')}}}
        with tempfile.TemporaryDirectory() as root, patch.object(decision_http, 'call', side_effect=score):
            result = run(child, market, anchor, Path(root), policy())
            replay = run(child, market, anchor, Path(root), policy())
        self.assertEqual(len(calls), 2)
        self.assertNotIn('research_map', calls[0])
        self.assertIn('research_map', calls[1])
        self.assertEqual(calls[0]['evidence'], calls[1]['evidence'])
        self.assertEqual(result, replay)
        self.assertTrue(result['arms']['mapped']['map_delivered'])


if __name__ == '__main__': unittest.main()
