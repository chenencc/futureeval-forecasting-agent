"""Check input isolation, grouped rules, deadlines and known ambiguity gates."""
import copy
import unittest

from ForecastAgent.market_pulse.inventory import inspect, SLUG, PROJECT_ID

NOW = '2026-10-08T10:45:28Z'


def fixture():
    metadata = {'id': PROJECT_ID, 'slug': SLUG, 'user_permission': 'forecaster',
                'questions_count_including_subquestions': 3}
    question = {'id': 123, 'title': 'First reported revenues (Example)', 'label': 'Example',
                'type': 'numeric', 'status': 'open', 'unit': '$',
                'open_time': '2026-10-07T20:00:00Z',
                'actual_close_time': '2026-10-21T10:00:00Z',
                'scheduled_close_time': '2026-10-21T10:00:00Z',
                'spot_scoring_time': '2026-10-19T12:00:00Z',
                'inbound_outcome_count': 200, 'open_lower_bound': True,
                'open_upper_bound': True,
                'scaling': {'range_min': 100., 'range_max': 200., 'zero_point': None},
                'resolution': None, 'aggregations': {'recency_weighted': {'latest': {'mean': 0.8}}},
                'my_forecasts': {'latest': {'forecast_values': [0.9]}},
                'resolution_criteria': '', 'fine_print': '', 'description': ''}
    post = {'id': 99, 'title': 'First reported revenues', 'user_permission': 'forecaster',
            'group_of_questions': {'resolution_criteria': 'Use first reported quarterly revenue.',
                'fine_print': 'Ignore later restatements.', 'description': 'Historical context.',
                'questions': [question]}}
    return metadata, [post]


class InventoryTests(unittest.TestCase):
    def test_parent_rules_and_blind_input(self):
        metadata, posts = fixture()
        original = copy.deepcopy(posts)
        report, inputs = inspect(metadata, posts, NOW)
        self.assertEqual(posts, original)
        request = inputs['123']['request']
        self.assertEqual(request['resolution_criteria'], 'Use first reported quarterly revenue.')
        self.assertEqual(report['rows'][0]['content_sources']['fine_print'], 'group_of_questions')
        self.assertNotIn('resolution', request)
        self.assertNotIn('aggregations', request)
        self.assertNotIn('my_forecasts', request)
        self.assertEqual(report['rows'][0]['format_probe_cdf_length'], 201)

    def test_missing_rules_are_not_title_inferred(self):
        metadata, posts = fixture()
        posts[0]['group_of_questions']['resolution_criteria'] = ''
        report, inputs = inspect(metadata, posts, NOW)
        self.assertEqual(report['format_valid'], 0)
        self.assertEqual(inputs, {})

    def test_conservative_spot_deadline_and_expiry(self):
        metadata, posts = fixture()
        report, _ = inspect(metadata, posts, '2026-10-20T00:00:00Z')
        self.assertEqual(report['platform_open_with_permission'], 1)
        self.assertEqual(report['automatic_candidates'], 0)
        self.assertTrue(report['rows'][0]['scoring_time_differs_from_close'])

    def test_hidden_count_is_not_open_count(self):
        metadata, posts = fixture()
        report, _ = inspect(metadata, posts, NOW)
        self.assertEqual(report['metadata_leaves_not_exposed_in_feed'], 2)
        self.assertEqual(report['unexposed_leaf_status'], 'unknown')
        self.assertEqual(report['platform_open_with_permission'], 1)

    def test_wrong_tournament_rejected(self):
        metadata, posts = fixture()
        metadata['id'] = 33121
        with self.assertRaises(ValueError):
            inspect(metadata, posts, NOW)

    def test_duplicate_child_rejected(self):
        metadata, posts = fixture()
        duplicate = copy.deepcopy(posts[0])
        duplicate['id'] = 100
        with self.assertRaises(ValueError):
            inspect(metadata, posts + [duplicate], NOW)

    def test_guidance_conflicting_quarters_require_review(self):
        metadata, posts = fixture()
        group = posts[0]['group_of_questions']
        group['questions'][0]['title'] = 'NVIDIA forward guidance in Q3 FY2027 release (Revenue)'
        group['resolution_criteria'] = 'Q4 FY2027 guidance in Q3 FY2027, expected August 26, 2026.'
        group['fine_print'] = 'Annul if no Q3 FY2027 guidance in Q2 FY2027 release.'
        report, _ = inspect(metadata, posts, NOW)
        self.assertEqual(report['format_valid'], 1)
        self.assertEqual(report['automatic_candidates'], 0)
        self.assertEqual(len(report['rows'][0]['rule_review']), 2)

    def test_amd_release_date_does_not_rewrite_deadline(self):
        metadata, posts = fixture()
        group = posts[0]['group_of_questions']
        group['questions'][0].update(label='AMD', title='First reported revenues (AMD)')
        group['resolution_criteria'] = 'AMD Q3 FY2026: November 3, 2026.'
        report, _ = inspect(metadata, posts, NOW)
        self.assertEqual(report['automatic_candidates'], 0)
        self.assertEqual(report['rows'][0]['official_times']['actual_close_time'], '2026-10-21T10:00:00Z')

    def test_closed_child_in_open_group_is_not_forecastable(self):
        metadata, posts = fixture()
        posts[0]['status'] = 'open'
        posts[0]['group_of_questions']['questions'][0]['status'] = 'closed'
        report, _ = inspect(metadata, posts, NOW)
        self.assertEqual(report['platform_open_with_permission'], 0)
        self.assertEqual(report['automatic_candidates'], 0)

    def test_permission_and_undated_deadline_fail_closed(self):
        metadata, posts = fixture()
        posts[0]['user_permission'] = 'viewer'
        report, _ = inspect(metadata, posts, NOW)
        self.assertEqual(report['automatic_candidates'], 0)
        posts[0]['user_permission'] = 'forecaster'
        question = posts[0]['group_of_questions']['questions'][0]
        for field in ('actual_close_time', 'scheduled_close_time', 'spot_scoring_time'):
            question[field] = None
        report, _ = inspect(metadata, posts, NOW)
        self.assertEqual(report['automatic_candidates'], 0)


if __name__ == '__main__':
    unittest.main()
