import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ForecastAgent.engineering.common import digest, encoded
from ForecastAgent.engineering.contracts import validate, ContractError
from ForecastAgent.engineering.snapshots import packet as export, resolve, open_readonly
from .fixtures import CLOCK, packet, competition, bundle


class Contracts(unittest.TestCase):
    def test_all_question_types(self):
        for kind in ('binary', 'multiple_choice', 'numeric', 'discrete', 'date'):
            with self.subTest(kind=kind):
                validate('packet', packet(kind))

    def test_forbidden_fields_fail_before_call(self):
        for field in ('resolution', 'forecast', 'future_price', 'authorization'):
            value = packet()
            value[field] = 'forbidden'
            with self.assertRaises(ContractError):
                validate('packet', value)

    def test_future_labels_cannot_hide_in_numeric_metadata(self):
        value=packet('numeric');value['question']['scale']['resolution']='FORBIDDEN'
        with self.assertRaises(ContractError):validate('packet',value)

    def test_wrong_original_hash_missing_rules_and_unknown_versions(self):
        for mutate in (lambda p: p['sources'][0].update(body_sha256='0'*64),
                       lambda p: p['question'].update(rules=''),
                       lambda p: p.update(schema='unregistered-v2')):
            p = packet(); mutate(p)
            with self.assertRaises(ContractError): validate('packet', p)

    def test_exact_identity_and_duplicate_source(self):
        p = packet(); p['question']['key'] = 'polymarket:123'
        with self.assertRaisesRegex(ContractError, 'identity'): validate('packet', p)
        p = packet(); p['sources'].append(copy.deepcopy(p['sources'][0]))
        with self.assertRaisesRegex(ContractError, 'duplicate'): validate('packet', p)

    def test_cutoff_rejects_future_unknown_and_missing_asof(self):
        for captured in (None, '2026-10-15T00:00:00Z'):
            p = packet(); p['sources'][0]['captured_at'] = captured
            with self.assertRaises(ContractError): validate('packet', p)
        p = packet(); p['as_of_utc'] = None
        with self.assertRaisesRegex(ContractError, 'cutoff_missing'): validate('packet', p)
        p = packet(); p['question']['observed_at'] = None
        with self.assertRaises(ContractError): validate('packet', p)

    def test_unknown_usage_is_null_not_zero(self):
        validate('usage', None)
        validate('usage', {'http_attempts': 1, 'input_tokens': None, 'output_tokens': None, 'cost_usd': None})
        with self.assertRaises(ContractError): validate('usage', {'http_attempts': -1})

    def test_numeric_forecasts_preserve_real_cdf_not_binary_clipping(self):
        value = {'schema':'forecast-experiment-output-v1', 'case_id':'x', 'input_sha256':'a'*64,
                 'kind':'numeric', 'value':[{'x': 1, 'p':0}, {'x':2, 'p':.5}, {'x':3,'p':1}]}
        validate('forecast', value)
        self.assertEqual(value['value'][0]['p'], 0)
        value['value'][1]['x'] = 1
        with self.assertRaises(ContractError): validate('forecast', value)

    def test_categorical_probability_sum(self):
        value = {'schema':'forecast-experiment-output-v1','case_id':'x','input_sha256':'a'*64,'kind':'multiple_choice','value':{'A':.2,'B':.8}}
        validate('forecast', value)
        value['value']['B'] = .7
        with self.assertRaises(ContractError): validate('forecast', value)


class OriginalSnapshots(unittest.TestCase):
    def test_competition_full_original_not_web_excerpt_and_no_future_fields(self):
        with tempfile.TemporaryDirectory() as root:
            database, bid = competition(root)
            before = database.read_bytes()
            result = export('competition', database, bid, 'metaculus:123', as_of_utc=CLOCK)
            self.assertEqual(result['sources'][0]['text'], next(iter(bundle()['pages'].values()))['content'])
            self.assertNotIn('FORBIDDEN', json.dumps(result))
            self.assertEqual(database.read_bytes(), before)
            self.assertTrue(result['gaps'])
            self.assertEqual(result['input_role'], 'sanitized_acquisition_snapshot_not_exact_legacy_model_input')

    def test_missing_exact_batch_and_wrong_question_never_fall_back_to_latest(self):
        with tempfile.TemporaryDirectory() as root:
            db, bid = competition(root)
            for ident, key in (('missing', 'metaculus:123'), (bid, 'metaculus:999')):
                with self.assertRaises(KeyError): export('competition', db, ident, key)

    def test_asof_filters_bodies_and_unknown_is_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            db, bid = competition(root)
            with self.assertRaises(ContractError): export('competition', db, bid, 'metaculus:123', as_of_utc='2026-10-13T00:00:00Z')
            p = export('competition', db, bid, 'metaculus:123')
            self.assertEqual(p['cutoff_policy'], 'capture_time_unbounded')
            self.assertIsNone(p['as_of_utc'])

    def test_tampered_original_is_rejected(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            path, bid = competition(root)
            db = sqlite3.connect(path)
            with db: db.execute("UPDATE native_objects SET bytes=X'0001'")
            db.close()
            with self.assertRaises(Exception): resolve('competition', path, bid, 'metaculus:123')

    def test_native_database_connection_is_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            path, _ = competition(root)
            with open_readonly(path) as db, self.assertRaises(Exception):
                db.execute('DELETE FROM bundles')
