"""Backend migration must never reset consumed acquisition budgets."""
from copy import deepcopy
import io
import json
import os
from unittest import TestCase
from unittest.mock import patch
from ForecastAgent.collection_campaign import prepare, read, write, run_batch, switch_model
from ForecastAgent.tests import test_collection_campaign
from ForecastAgent.provider_health import probe

SUPER = 'nvidia/nemotron-3-super-120b-a12b:free'


class ModelMigrationTests(TestCase):
    def setUp(self):
        self.fixture = test_collection_campaign.CampaignTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_migration_preserves_tasks_inputs_resources_and_old_transport(self):
        f = self.fixture
        prepare(f.root, f.fixture, 2)
        run_batch(f.root, 1, f.runner)
        before = deepcopy(read(f.root/'campaign.json'))
        body = (f.root/'tasks'/'1'/'bundle.json').read_bytes()
        with patch.dict(os.environ, {'FORECAST_MODEL': SUPER}):
            with self.assertRaisesRegex(ValueError, 'Frozen campaign model'):
                run_batch(f.root, 1, f.runner)
            switch_model(f.root, 'User authorized a verified free Super backend migration.')
            after = read(f.root/'campaign.json')
            self.assertEqual(after['tasks'], before['tasks'])
            self.assertEqual(after['requests'], before['requests'])
            self.assertEqual(after['limits'], before['limits'])
            self.assertEqual(after['model'], SUPER)
            self.assertEqual(after['initial_model'], before['model'])
            self.assertFalse(after['model_migrations'][0]['budget_reset'])
            self.assertEqual(body, (f.root/'tasks'/'1'/'bundle.json').read_bytes())
            switch_model(f.root, 'Repeated authorization is idempotent and preserves budgets.')
            self.assertEqual(len(read(f.root/'campaign.json')['model_migrations']), 1)
            result = run_batch(f.root, 1, f.runner)
            self.assertEqual(result['resources']['tavily_basic'], 2)

    def test_migration_requires_reason_and_no_running_tasks(self):
        f = self.fixture
        prepare(f.root, f.fixture, 1)
        with patch.dict(os.environ, {'FORECAST_MODEL': SUPER}):
            with self.assertRaises(ValueError): switch_model(f.root, 'Switch')
            before = read(f.root/'campaign.json'); before['tasks']['1']['status'] = 'running'
            write(f.root/'campaign.json', before)
            with self.assertRaisesRegex(ValueError, 'running tasks'):
                switch_model(f.root, 'User authorized a verified free Super backend migration.')
            self.assertEqual(read(f.root/'campaign.json'), before)

    def test_switch_alone_does_not_clear_provider_pause(self):
        f = self.fixture
        prepare(f.root, f.fixture, 1)
        before = read(f.root/'campaign.json'); before['pause_until_utc'] = '2026-12-01T00:00:00Z'
        write(f.root/'campaign.json', before)
        with patch.dict(os.environ, {'FORECAST_MODEL': SUPER}):
            switch_model(f.root, 'User authorized a verified free Super backend migration.')
        self.assertEqual(read(f.root/'campaign.json')['pause_until_utc'], before['pause_until_utc'])

    def test_health_probe_uses_configured_backend(self):
        response = io.BytesIO(json.dumps({'choices': [{'message': {'tool_calls': [
            {'function': {'name': 'report_health', 'arguments': '{"ready":true}'}}]}}]}).encode())
        response.status = 200
        with patch.dict(os.environ, {'FORECAST_MODEL': SUPER}):
            result = probe('test', opener=lambda *args, **kwargs: response)
        self.assertTrue(result['healthy'])
        self.assertEqual(result['request']['model'], SUPER)
