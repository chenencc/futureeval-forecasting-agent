"""Rule bindings, exact saved evidence, program routing and durable request budgets."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis import p0
from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.tests.test_mercury_evidence_chain import response


def bundle():
    official='August 10, 2026 release. July observation: Current Index 45.7 points.\n'+'Monthly table | Current Index | points | July 2026 | 45.7\n'*250
    other='The August 2026 Current Index reached 46.4 points; published September 10, 2026.\n'*250
    return {'request':{'id':'1','question':'What is the Current Index in the August release?',
            'question_type':'binary','resolution_criteria':'Use the first release date occurring in August 2026, https://agency.gov/report.pdf',
            'fine_print':'The release may concern the previous observation month.'},
            'pages':{u:{'content':body,'content_sha256':hashlib.sha256(body.encode()).hexdigest()} for u,body in
                [('https://agency.gov/report.pdf',official),('https://news.org/latest',other)]},'gaps':[]}


class AnalysisP0Tests(unittest.TestCase):
    def test_multilingual_spans_preserve_every_character_under_serialized_size_bound(self):
        import json
        b=bundle();body='令和８年７月調査結果 令和８年８月10 日 現状判断45.7。'*150
        b['pages']={'https://agency.gov/jp':{'content':body}}
        packet=p0.balanced_packet(b)
        self.assertEqual(''.join(s['text'] for s in packet['evidence']),body)
        self.assertTrue(all(len(json.dumps(s['text']).encode())<=1800 for s in packet['evidence']))
        for s in packet['evidence']:self.assertEqual(s['text'],body[s['start']:s['end']])
    def test_contract_quotes_exact_fields_without_inventing_dates(self):
        b=bundle();rule=p0.contract(b['request'])
        self.assertEqual(rule['time_basis'],'release_window')
        for quote in rule['time_constraints']+rule['stage_and_exception_constraints']:
            self.assertEqual(quote['quote'],b['request'][quote['field']][quote['start']:quote['end']])
        self.assertFalse(p0.contract({'question':'Will an event occur?'})['date_mentions'])

    def test_directed_reading_retains_official_measurement_and_original_offsets(self):
        b=bundle();packet=chain.full_packet(b);rule=p0.contract(b['request'])
        first,audit=p0.directed_select(packet,rule)
        self.assertLessEqual(audit['request_bytes'],chain.FIRST_BYTES)
        self.assertTrue(any('July observation: Current Index 45.7' in s['text'] for s in first['evidence']))
        for s in first['evidence']:self.assertEqual(s['text'],b['pages'][s['url']]['content'][s['start']:s['end']])
        second,audit=p0.directed_select(packet,rule,first,['value_units'],chain.SECOND_BYTES)
        self.assertTrue(all(s in second['evidence'] for s in first['evidence']))
        self.assertLessEqual(audit['request_bytes'],chain.SECOND_BYTES)
        self.assertNotIn('answers',second)

    def test_program_routes_release_ambiguity_despite_confident_model(self):
        b=bundle();packet=chain.full_packet(b);rule=p0.contract(b['request'])
        first,_=p0.directed_select(packet,rule)
        self.assertEqual(chain.route(response(False)),[])
        audit=p0.coverage(packet,first,rule,'binary')
        self.assertIn('release_observation_distinction',audit['program_reasons'])
        self.assertFalse(audit['semantic_truth_verified'])

    def test_second_failure_preserves_validated_first_and_never_resets(self):
        b=bundle();before=copy.deepcopy(b)
        with tempfile.TemporaryDirectory() as root,patch.object(chain,'call',side_effect=[response(False),RuntimeError('service unavailable')]) as call:
            r=p0.run_task(b,Path(root))
            self.assertEqual(call.call_count,2);self.assertEqual(r['probability_yes'],.4)
            self.assertTrue(r['second_error']);self.assertEqual(b,before)
            altered=copy.deepcopy(b);altered['request']['fine_print']+=' changed'
            with self.assertRaisesRegex(ValueError,'Frozen'):p0.run_task(altered,Path(root))


if __name__=='__main__':unittest.main()
