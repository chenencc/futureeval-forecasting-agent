"""Native capability metadata and standalone boundary regression tests."""
import unittest
from copy import deepcopy
from ForecastAgent.tools.intelligence_box.compatibility import specs,capabilities,compatibility_manifest,NETWORK_NAMES,PENDING_NETWORK_NAMES
from ForecastAgent.tools.intelligence_box.core import TOOL_DEFINITIONS

class CompatibilityTests(unittest.TestCase):
    def test_new_network_tools_are_not_misclassified_as_saved_reads(self):
        rows=specs(); self.assertEqual(len(rows),12)
        self.assertFalse(PENDING_NETWORK_NAMES & {s['id'] for s in rows})
        for pending in compatibility_manifest()['pending_tools']:
            self.assertIn('network',pending['effects']); self.assertFalse(pending['exposed'])

    def test_native_network_tools_require_needs_and_share_native_budget(self):
        for row in specs():
            schema=row['definition']['function']['parameters']
            if row['id'] in NETWORK_NAMES:
                self.assertEqual(row['budgets'],('initial_http',))
                self.assertIn('need_ids',schema['required'])
                self.assertEqual(schema['properties']['need_ids']['maxItems'],8)
                self.assertIn('material_write',row['effects'])
            else: self.assertEqual(row['budgets'],())

    def test_navigation_retains_native_url_and_coordinate_adapter(self):
        for row in specs():
            if row['id'] in {'intelligence_outline','intelligence_search','intelligence_part'}:
                schema=row['definition']['function']['parameters']
                self.assertIn('url',schema['properties']); self.assertNotIn('capture_id',schema['required'])
                self.assertEqual(row['handler'],'ForecastAgent.channels.native:execute')
        part=next(s for s in specs() if s['id']=='intelligence_part')
        self.assertEqual(part['effects'],('saved_read','material_write'))

    def test_does_not_mutate_standalone_definitions_or_register_implicitly(self):
        prior=deepcopy(TOOL_DEFINITIONS)
        rows=capabilities(factory=lambda **values:values)
        self.assertEqual(len(rows),12); self.assertEqual(prior,TOOL_DEFINITIONS)
        rows[0]['definition']['function']['description']='changed'
        self.assertEqual(prior,TOOL_DEFINITIONS)
        self.assertFalse(compatibility_manifest()['registration_grants_budget'])

if __name__=='__main__': unittest.main()
