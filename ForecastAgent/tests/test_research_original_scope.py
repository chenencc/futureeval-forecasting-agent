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
        self.assertEqual(len(data['reading']['source_groups']),2)
        self.assertIn('do not answer the question',data['task'])

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

    def test_representation_and_record_metadata_survive_without_meaning_credit(self):
        supplied = packet()
        metadata = {'coordinate_space':'decoded_post', 'view_sha256':'c'*64,
                    'parser':'saved_decoder', 'json_provenance':{'post_id':123},
                    'reference_kind':'literal_context_window',
                    'constituent_reference_ids':['Rprimitive'],
                    'context_policy':'record_delimited_original_windows_v2',
                    'record_delimiter':'multiline_markdown_link_end',
                    'leading_record_context_bounded':True, 'record_relation_verified':False}
        supplied['evidence'][0].update(metadata)
        before = copy.deepcopy(supplied)
        reading = scope.bind({'facts':[fact()]}, supplied)
        binding = reading['facts'][0]['bindings'][0]
        for key, value in metadata.items():
            self.assertEqual(binding[key], value)
        self.assertFalse(reading['facts'][0]['date_role_verified'])
        self.assertFalse(reading['facts'][0]['entity_role_verified'])
        self.assertFalse(binding['record_relation_verified'])
        self.assertEqual(supplied, before)

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

    def test_empty_facts_are_valid_and_packet_gaps_are_not_observations(self):
        source=scope.source_groups(packet())[0]
        gap={'source_ids':[source['source_id']], 'kind':'future_unpublished',
            'note':'Supplied material does not contain the future event outcome.'}
        reading=scope.bind({'facts':[], 'gaps':[gap]},packet())
        self.assertEqual(reading['facts'],[])
        self.assertEqual(reading['rejected'],[])
        self.assertFalse(reading['gaps'][0]['provides_fact_evidence'])
        self.assertEqual(reading['gaps'][0]['source_bindings'][0],source)
        self.assertFalse(reading['gaps'][0]['interpretation_verified'])

    def test_same_url_different_capture_versions_cannot_be_one_fact(self):
        data=packet()
        data['context_evidence'][0]['url']=data['evidence'][0]['url']
        combined=fact();combined['reference_ids']=['Ra','Rb']
        reading=scope.bind({'facts':[combined,fact()], 'gaps':[]},data)
        self.assertEqual(len(scope.source_groups(data)),2)
        self.assertEqual(len(reading['facts']),1)
        self.assertEqual(len(reading['rejected']),1)

    def test_unknown_gap_sources_do_not_discard_valid_source_facts(self):
        gap={'source_ids':['not-delivered'], 'kind':'insufficient_context', 'note':'Unknown context.'}
        reading=scope.bind({'facts':[fact()], 'gaps':[gap]},packet())
        self.assertEqual(len(reading['facts']),1)
        self.assertEqual(reading['gaps'],[])
        self.assertEqual(len(reading['rejected_gaps']),1)

    def test_long_facts_are_preserved_as_rejections_never_clipped(self):
        long=fact();long['note']='x'*241
        reading=scope.bind({'facts':[long], 'gaps':[]},packet())
        self.assertEqual(reading['facts'],[])
        self.assertEqual(len(reading['rejected']),1)
        self.assertEqual(long['note'],'x'*241)


if __name__=='__main__':unittest.main()
