"""Real request journal replay and immutable original-body comparison contracts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis import enhanced_pair40 as trial, mercury_evidence_chain as chain
from ForecastAgent.analysis.pilot import load
from ForecastAgent.supplement.enhanced import hints
from ForecastAgent.tests.test_competition_mercury import bundle, response


class PairTests(unittest.TestCase):
    def test_actual_request_journal_cache_and_same_packet(self):
        source = bundle(); packet = chain.full_packet(source)
        item = {'id': '7', 'cohort': 'binary', 'packet': packet,
                'questions': chain.questions(), 'spec': None, 'reading_hints': hints(source)}
        calls = []
        def decide(state, registry, key, observer):
            record = {'request': {'model': 'inception/mercury-decide:free', 'state': state, 'questions': registry}, 'status': 'reserved'}
            token = observer('reserve', record); record.update(status='received', response=response(registry))
            observer('complete', record, token); calls.append(1)
            return record['response']
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline'}), patch('ForecastAgent.providers.decisions.decide', decide):
            old = trial.route(item, 'old', Path(tmp)/'old')
            new = trial.route(item, 'new', Path(tmp)/'new')
            again = trial.route(item, 'new', Path(tmp)/'new')
            self.assertEqual(new, again); self.assertEqual(len(calls), 2)
            self.assertEqual(old['http_attempts'], 1); self.assertEqual(new['http_attempts'], 1)
            self.assertEqual(load(Path(tmp)/'old/first/request.json')['questions'], load(Path(tmp)/'new/first/request.json')['questions'])

    def test_changed_original_span_rejected(self):
        packet = chain.full_packet(bundle()); packet['evidence'][0]['text'] += 'tampered'
        with self.assertRaisesRegex(ValueError, 'span|hash'): trial.verify_packet(packet)


if __name__ == '__main__': unittest.main()
