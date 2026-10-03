"""Expanded collection stays opt-in, frozen and outcome-free."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.runtime.capacity import DEFAULT, SOLID, freeze
from ForecastAgent.runtime.guidance import collection_system
from ForecastAgent.experiments import solid_collection as pilot
from ForecastAgent.analysis.pilot import load
from ForecastAgent.supplement import enhanced


class SolidTests(unittest.TestCase):
    def test_expanded_profile_matches_program_and_prompt_and_resume(self):
        request = pilot.question('44126', 'offline-test')
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'EXA_API_KEY': 'offline'}):
            task = RetrievalTask(Path(tmp), request)
            self.assertEqual(task.budget()['page_fetch_remaining'], 32)
            self.assertEqual(task.search_limit, 6)
            self.assertEqual(task.exa_limit, 2)
            system = collection_system(task, [])
            self.assertIn('6 actual Tavily attempts and 2 actual Exa attempts', system)
            self.assertNotIn('Within EIGHT', system)
            task.bundle['fetch_attempts'].append({'status': 'reserved'})
            task.save()
            self.assertEqual(RetrievalTask(Path(tmp), request).budget()['page_fetch_remaining'], 31)

    def test_profiles_require_experiment_and_cannot_mutate(self):
        with self.assertRaises(ValueError):
            freeze({}, {'budget_profile': 'solid_v1'}, False)
        bundle = {'capacity': dict(DEFAULT)}
        with self.assertRaises(ValueError):
            freeze(bundle, {'budget_profile':'solid_v1','experiment_id':'x','pipeline':'collection'}, True)

    def test_five_questions_are_fresh_and_outcome_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            for case, ident in enumerate(pilot.IDS, 1):
                root = Path(tmp)/ident
                manifest = pilot.run(case, root, 'offline-test', True)
                self.assertEqual(manifest['capacity'], SOLID)
                request = load(root/'question.json')
                self.assertFalse({'resolution','probability','historical_snapshot_bundle','as_of_utc'} & set(request))
                self.assertFalse((root/'acquisition/bundle.json').exists())
                self.assertEqual(pilot.run(case, root, 'offline-test', True), manifest)
                with self.assertRaises(ValueError):
                    pilot.run(case, root, 'different-experiment', True)

    def test_detail_frontier_is_depth_bounded_on_restart(self):
        from ForecastAgent.tests.test_enhanced_supplement import bundle
        source = bundle()
        source['request']['experiment_id'] = 'offline-test'
        urls = ['https://example.org/desktop', 'https://example.org/desktop-1',
                'https://example.org/desktop-2', 'https://example.org/desktop-3']
        def fetch(url):
            index = urls.index(url)
            return {'content':'August 2026 worldwide desktop Linux share was 8.4%.',
                    'links':[{'url':urls[index+1], 'text':'August Linux desktop details'}] if index<3 else []}
        with tempfile.TemporaryDirectory() as tmp, patch.object(enhanced, 'fetch_document', side_effect=fetch) as http:
            enhanced.run(source, tmp, network=True, caps=pilot.REPAIR_CAPS, max_link_depth=2)
            self.assertEqual(http.call_count, 3)
            enhanced.run(source, tmp, network=True, caps=pilot.REPAIR_CAPS, max_link_depth=2)
            self.assertEqual(http.call_count, 3)


if __name__ == '__main__':
    unittest.main()
