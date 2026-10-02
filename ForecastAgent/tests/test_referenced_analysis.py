"""Evidence-reference provenance and scope-gap regression checks."""
import copy
import hashlib
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.referenced import evidence_packet, read_saved, parse, materialize, assess, FIELDS
from ForecastAgent.analysis import referenced


class ReferencedAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.body = 'The July observation was 7.51 percent.\n' * 600
        self.bundle = {'request': {'id': '1', 'question': 'Was the August worldwide share above 8 percent?',
                                   'resolution_criteria': 'August worldwide share must be above 8 percent.', 'resolution': 1},
                       'pages': {'https://example.org': {'content': self.body, 'content_sha256': hashlib.sha256(self.body.encode()).hexdigest()}}}
        self.packet = evidence_packet(self.bundle)
        self.report = {key: 'Unknown' for key in FIELDS}
        self.report.update(reasoning_probability_yes=.4, gaps=['August observation is missing'],
            facts=[{'claim': 'July observation', 'evidence_refs': ['E0001'], 'supports': 'context'}],
            conditions=[{'condition_id': 'C1', 'requirement': 'August worldwide observation', 'evidence_refs': ['E0001'],
                         'coverage': 'partial', 'status': 'uncertain', 'observation_window': 'July only', 'gap': 'August missing'}])

    def message(self):
        return {'tool_calls': [{'function': {'name': 'record_analysis', 'arguments': json.dumps(self.report)}}]}

    def test_program_materializes_exact_spans(self):
        report = parse(self.message(), self.packet)
        for item in materialize(self.packet, report):
            self.assertEqual(self.body[item['start']:item['end']], item['text'])
        self.assertNotIn('resolution', self.packet['question'])

    def test_invented_reference_is_rejected(self):
        self.report['facts'][0]['evidence_refs'] = ['E9999']
        with self.assertRaises(ValueError):
            parse(self.message(), self.packet)

    def test_declared_full_coverage_with_gap_is_not_complete(self):
        self.report['conditions'][0].update(coverage='full', status='supported')
        quality = assess(self.packet, self.report, True)
        self.assertFalse(quality['all_necessary_conditions_complete'])
        self.assertEqual(quality['status'], 'review_required')

    def test_failed_transport_usage_is_not_zero_cost(self):
        from ForecastAgent.analysis.evaluation import transport_usage
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'ultra-http').mkdir()
            records = [
                {'request': {'model': 'test:free'}, 'status': 'received', 'response': {'usage': {'total_tokens': 123}}},
                {'request': {'model': 'test:free'}, 'status': 'missing_choices', 'response': {'error': {'code': 502}}},
            ]
            for index, record in enumerate(records):
                (root / 'ultra-http' / f'{index}.json').write_text(json.dumps(record), encoding='utf-8')
            usage = transport_usage(root)
            self.assertEqual(usage['reasoning_http_attempts'], 2)
            self.assertEqual(usage['known_tokens'], 123)
            self.assertEqual(usage['attempts_with_unknown_usage'], 1)

    def test_analysis_route_restores_service_failures_and_sticky_fallback(self):
        from ForecastAgent.providers.model import ULTRA_MODEL, SUPER_MODEL
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / 'tasks' / '1' / 'ultra-http'
            folder.mkdir(parents=True)
            for index in range(2):
                record = {'started_at_utc': str(index), 'request': {'model': ULTRA_MODEL},
                          'status': 'missing_choices', 'response': {'error': {'code': 502}}}
                (folder / f'{index}.json').write_text(json.dumps(record), encoding='utf-8')
            route = referenced.restore_route(temporary)
            self.assertEqual(route.model(), SUPER_MODEL)
            route.observe({'status': 'received'})
            self.assertEqual(route.model(), SUPER_MODEL)
            self.assertEqual(len(list(folder.glob('*.json'))), 2)

    def test_account_errors_do_not_enable_analysis_fallback(self):
        from ForecastAgent.providers.model import ULTRA_MODEL
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / 'tasks' / '1' / 'ultra-http'
            folder.mkdir(parents=True)
            for index in range(2):
                record = {'request': {'model': ULTRA_MODEL}, 'status': 'missing_choices',
                          'response': {'error': {'code': 429}}}
                (folder / f'{index}.json').write_text(json.dumps(record), encoding='utf-8')
            self.assertEqual(referenced.restore_route(temporary).model(), ULTRA_MODEL)

    def test_full_condition_requires_source_reference(self):
        self.report['conditions'][0].update(coverage='full', evidence_refs=[])
        with self.assertRaises(ValueError):
            parse(self.message(), self.packet)

    def test_missing_month_requires_review_without_changing_probability(self):
        result = assess(self.packet, self.report, True)
        self.assertEqual(result['status'], 'review_required')
        self.assertFalse(result['automated_use_eligible'])
        self.assertEqual(self.report['reasoning_probability_yes'], .4)

    def test_local_read_stays_bound_to_saved_body(self):
        added = read_saved(self.bundle, self.packet, {'source_id': 'S1', 'start': 12000, 'length': 2000})
        self.assertEqual(len({e['evidence_id'] for e in self.packet['evidence']}), len(self.packet['evidence']))
        for item in added:
            self.assertEqual(self.body[item['start']:item['end']], item['text'])
        self.bundle['pages']['https://example.org']['content'] += 'changed'
        with self.assertRaises(ValueError):
            read_saved(self.bundle, self.packet, {'source_id': 'S1', 'start': 0, 'length': 100})

    def test_resume_after_scoring_crash_reuses_completed_http(self):
        counters = {'reasoning': 0, 'decision': 0}

        def reasoning(messages, key, observer, **kwargs):
            counters['reasoning'] += 1
            message = self.message()
            record = {'status': 'reserved', 'request': {'model': 'offline-model:free'}}
            token = observer('reserve', record)
            record.update(status='received', response={'choices': [{'message': message}], 'usage': {'total_tokens': 12}})
            observer('complete', record, token)
            return message

        def decision(state, questions, key, observer):
            counters['decision'] += 1
            response = {'model': 'inception/mercury-decide:free', 'answers': {'event_yes': {'noul': .4}, 'evidence_sufficiency': {'score': 1.0}}}
            record = {'status': 'reserved', 'request': {'model': 'inception/mercury-decide:free'}}
            token = observer('reserve', record)
            record.update(status='received', response=response)
            observer('complete', record, token)
            return response

        original_save = referenced.save
        def crash_on_prediction(path, value):
            if Path(path).name == 'prediction.json':
                raise OSError('Simulated interruption after decision persistence')
            original_save(path, value)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            output = Path(directory) / 'analysis'
            original_save(root / 'campaign.json', {'tasks': {'1': {'status': 'acquired'}}})
            original_save(root / 'tasks' / '1' / 'bundle.json', self.bundle)
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline-placeholder', 'FORECAST_MODEL': 'offline-model:free'}), \
                 patch('ForecastAgent.providers.ultra.ask_ultra', side_effect=reasoning), patch.object(referenced, 'decide', side_effect=decision):
                with patch.object(referenced, 'save', side_effect=crash_on_prediction), self.assertRaises(RuntimeError):
                    referenced.run(root, output, ['1'])
                self.assertEqual(counters, {'reasoning': 2, 'decision': 1})
                referenced.run(root, output, ['1'])
                self.assertEqual(counters, {'reasoning': 2, 'decision': 1})
                changed = copy.deepcopy(self.bundle)
                changed['request']['question'] = 'Changed question'
                original_save(root / 'tasks' / '1' / 'bundle.json', changed)
                with self.assertRaises(RuntimeError):
                    referenced.run(root, output, ['1'])
                self.assertEqual(counters, {'reasoning': 2, 'decision': 1})


if __name__ == '__main__':
    unittest.main()
