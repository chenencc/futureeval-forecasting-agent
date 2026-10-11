"""Independent original reading excludes old maps and binds exact supplied spans."""
import copy
import json
import unittest
from ForecastAgent.research_loop import original_scope as scope


def packet():
    return {'evidence':[{'evidence_id':'Ra','url':'https://example.org/a','body_sha256':'a'*64,
        'start':0,'end':18,'text':'Published Sept16.'}],
        'context_evidence':[{'evidence_id':'Rb','url':'https://example.org/b','body_sha256':'b'*64,
        'start':0,'end':16,'text':'City districts.'}],
        'context_reference_ids':{},'scope':'Bounded originals',
        'existing_node_reading_scope':[{'node_id':'old'}], 'identifier_registry':{'current_node_ids':['old']}}


def fact(ref='Ra'):
    return {'reference_ids':[ref],'date_role':'publication','entity_role':'unknown','note':'Date labels article publication; event date unknown.'}


class OriginalScopeTests(unittest.TestCase):
    def test_blind_request_never_includes_old_map_or_processing_labels(self):
        messages=scope.messages({'request':{'question':'Future event?'},
            'research_loop':{'current':{'nodes':[{'interpretation':'Secret wrong old claim'}]}}},packet())
        data=json.loads(messages[1]['content'])
        self.assertNotIn('Secret wrong old claim',messages[1]['content'])
        self.assertNotIn('existing_node_reading_scope',data['reading'])
        self.assertNotIn('identifier_registry',data['reading'])
        self.assertEqual(data['reading']['evidence'],packet()['evidence'])

    def test_valid_originals_bound_but_date_and_entity_roles_unverified(self):
        reading=scope.bind({'facts':[fact()]},packet())
        row=reading['facts'][0]
        self.assertEqual(row['bindings'][0]['text'],packet()['evidence'][0]['text'])
        self.assertTrue(row['original_reference_verified'])
        self.assertFalse(row['date_role_verified'])
        self.assertFalse(row['entity_role_verified'])
        projected=scope.for_review(reading)
        self.assertNotIn('text',projected['facts'][0]['bindings'][0])
        self.assertIn('text',reading['facts'][0]['bindings'][0])

    def test_invalid_siblings_isolated_without_combining_sources_or_guessing_refs(self):
        cross=fact();cross['reference_ids']=['Ra','Rb']
        duplicate=fact();duplicate['reference_ids']=['Ra','Ra']
        before=copy.deepcopy(packet())
        reading=scope.bind({'facts':[fact(),cross,duplicate,fact('Rmissing')]},before)
        self.assertEqual(len(reading['facts']),1)
        self.assertEqual(len(reading['rejected']),3)
        self.assertEqual(before,packet())

    def test_unbounded_container_is_rejected(self):
        with self.assertRaises(ValueError):scope.bind({'facts':[fact()]*9},packet())


if __name__=='__main__':unittest.main()
