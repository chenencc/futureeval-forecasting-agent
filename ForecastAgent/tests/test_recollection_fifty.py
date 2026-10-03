"""Explicit extensions retain reservations and cannot change frozen parents."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.supplement.stage import extend_budget, run
from ForecastAgent.tests.test_supplement import SupplementTests
from ForecastAgent.supplement.recollection_fifty import execute
from ForecastAgent.supplement.discovery import discover


class FreeExtensionTests(unittest.TestCase):
    def test_only_monotonic_caps_and_identical_inputs(self):
        old = {'browser_limit_per_task':2,'http_limit_per_task':2,'parent':'fixed'}
        new = dict(old,browser_limit_per_task=4,http_limit_per_task=10)
        self.assertTrue(extend_budget(old,new,'authorized'))
        self.assertFalse(extend_budget(old,new,None))
        self.assertFalse(extend_budget(old,dict(new,parent='changed'),'authorized'))
        self.assertFalse(extend_budget(old,dict(new,http_limit_per_task=11),'authorized'))
        self.assertFalse(extend_budget(old,dict(new,http_limit_per_task=1),'authorized'))

    def test_failed_reservation_preserved_under_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive,_ = SupplementTests().fixture(root)
            with patch('ForecastAgent.supplement.stage.render_page',side_effect=RuntimeError('failed')) as browser:
                run(archive,root/'out',['1'],network=True)
                old = json.loads((root/'out/tasks/1/supplement.json').read_text())['attempts']
                run(archive,root/'out',['1'],network=True,http_limit=10,browser_limit=4,budget_extension_reason='authorized')
                run(archive,root/'out',['1'],network=True,http_limit=10,browser_limit=4,budget_extension_reason='authorized')
                self.assertEqual(browser.call_count,1)
                self.assertEqual(json.loads((root/'out/tasks/1/supplement.json').read_text())['attempts'],old)
                self.assertEqual(len(json.loads((root/'out/budget-extensions.json').read_text())),1)

    def test_completed_fifty_scope_is_required_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'parent/handoffs').mkdir(parents=True)
            with patch('ForecastAgent.supplement.recollection_fifty.run') as repair:
                with self.assertRaisesRegex(ValueError,'exactly fifty'):
                    execute(root/'parent',root/'out',0,network=True)
                repair.assert_not_called()

    def test_generic_approval_is_not_entity_match(self):
        bundle={'request':{'question':'Will Starlink gain regulatory approval in India?'},
                'searches':[{'results':[{'url':'https://news.example/merger','title':'Regulatory approval announced for unrelated merger'}]}]}
        plan=discover(bundle,[{'url':'https://starlink.example/map','category':'access_restricted'}])
        self.assertEqual(plan['gaps'][0]['candidates'],[])


if __name__=='__main__':
    unittest.main()
