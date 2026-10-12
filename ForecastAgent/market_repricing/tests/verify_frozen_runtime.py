"""Exercise the actual immutable release selector with zero provider requests."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.market_repricing.common import now, policy
from ForecastAgent.market_repricing.runtime import activate


class FrozenRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        activate(os.environ['MARKET_RELEASE_DIRECTORY'])

    def test_originals_and_price_heads_on_real_release(self):
        from ForecastAgent.market_repricing.forecast import run
        from ForecastAgent.analysis import mercury_evidence_chain as chain
        from ForecastAgent.acquisition.pipeline import prepare
        from ForecastAgent.market_repricing.worker import request_for
        from ForecastAgent.market_repricing.venues import normalize
        from ForecastAgent.market_repricing.tests.test_repricing import raw_poly
        market = normalize('polymarket', raw_poly())
        request, warnings = prepare(request_for(market))
        text = ('Federal Reserve meeting notice. December 10, 2026 is the scheduled meeting date. '
                'The target range is currently unchanged. This notice does not announce a rate cut.\n') * 30
        bundle = {'request': request, 'pages': {'https://example.org/fed': {'content': text,
                  'body_diagnostics': {'usable_text': True}, 'retrieved_at_utc': now()}},
                  'gaps': [{'code': 'future_decision_unknown'}]}
        anchor = {'status': 'usable', 'received_at': now(), 'midpoint': .45, 'yes_bid': .43, 'yes_ask': .47}
        seen = []
        def decision(state, root, questions):
            seen.append(state)
            answers = {}
            for key, q in questions.items():
                if q['type'] == 'noul': answers[key] = {'type': 'noul', 'noul': .5}
                elif q['type'] == 'score':
                    dist = {str(i): 1 if i == 2 else 0 for i in range(len(q['criteria']))}
                    answers[key] = {'type': 'score', 'score': 2, 'confidence': 1, 'probabilities': dist}
                else:
                    chosen = 'p12' if key.startswith('price_') else ('supported' if 'supported' in q['criteria'] else next(iter(q['criteria'])))
                    answers[key] = {'type': 'choice', 'choice': chosen, 'confidence': 1,
                                    'probabilities': {k: 1 if k == chosen else 0 for k in q['criteria']}}
            # No contradiction and no material conflict: no conditional second request.
            answers['material_conflict']['noul'] = 0
            return {'model': 'inception/mercury-decide:free', 'answers': answers}
        with tempfile.TemporaryDirectory() as root:
            with patch.object(chain, 'call', side_effect=decision), patch('ForecastAgent.market_repricing.forecast.brief', return_value={'claims': [], 'gaps': [], 'catalysts': []}):
                result = run(bundle, market, anchor, Path(root), policy())
                again = run(bundle, market, anchor, Path(root), policy())
            self.assertEqual(len(seen), 2)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['arms']['mercury_original']['price_means']['72'], .6)
            self.assertEqual(result, again)
            self.assertEqual(seen[0]['evidence'], seen[1]['evidence'])
            for span in seen[0]['evidence']:
                self.assertEqual(text[span['start']:span['end']], span['text'])


if __name__ == '__main__': unittest.main()
