"""Manual approval is scoped to exact candidates and known review conditions."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ForecastAgent.analysis.pilot import save
from ForecastAgent.market_pulse import analysis, held_delivery as d
from ForecastAgent.market_pulse.tests.test_held_review import guidance_request


class ReviewedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name) / 'state.json'
        save(state, {'original_evidence': 'unchanged'})
        self.entry = {'id': '46248', 'post_id': 46016, 'manual_review_authorized': True,
            'state_path': str(state), 'state_sha256': analysis.sha(state),
            'reviewed_codes': list(d.REVIEW_CODES), 'target_period': 'Q4 FY2027',
            'metric': 'revenue_guidance', 'known_region_disagreement': .134}
        self.row = {'question_id': '46248', 'platform_open_with_permission': True,
            'input_valid': True, 'conservative_deadline_utc': '2099-11-17T00:00:00Z',
            'rule_review': [{'code': c} for c in d.REVIEW_CODES], 'automatic_candidate': False}
        self.request = guidance_request()

    def check(self):
        with patch.object(d.inventory, 'inspect', return_value=({'rows': [self.row]},
                {'46248': {'request': self.request}})), \
                patch.object(d, 'questions', return_value=[{'id': 46248}]), \
                patch.object(d, 'compare', return_value={'id': '46248'}):
            return d.authorized_check(self.entry, {}, {})

    def test_known_explicit_review_does_not_mutate_automatic_inventory(self):
        prior = copy.deepcopy(self.row)
        result = self.check()
        self.assertTrue(result['manual_review_authorized'])
        self.assertEqual(self.row, prior)
        self.assertFalse(self.row['automatic_candidate'])
        self.assertEqual(result['original_rule_review_preserved'], prior['rule_review'])

    def test_new_review_condition_cannot_be_silently_approved(self):
        self.row['rule_review'].append({'code': 'new_issue'})
        with self.assertRaisesRegex(ValueError, 'Fresh review conditions'):
            self.check()

    def test_closed_or_expired_leaf_cannot_use_manual_approval(self):
        self.row['platform_open_with_permission'] = False
        with self.assertRaisesRegex(ValueError, 'Platform status'):
            self.check()
        self.row['platform_open_with_permission'] = True
        self.row['conservative_deadline_utc'] = '2025-01-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'deadline'):
            self.check()

    def test_annulled_revenue_and_unapproved_scope_are_excluded(self):
        self.entry['id'] = '46198'
        with self.assertRaisesRegex(ValueError, 'authorized guidance scope'):
            self.check()
        self.entry['id'] = '46248'
        self.entry['manual_review_authorized'] = False
        with self.assertRaisesRegex(ValueError, 'authorized guidance scope'):
            self.check()

    def test_changed_original_evidence_is_rejected(self):
        save(Path(self.entry['state_path']), {'changed': True})
        with self.assertRaisesRegex(ValueError, 'evidence exposure changed'):
            self.check()


if __name__ == '__main__':
    unittest.main()
