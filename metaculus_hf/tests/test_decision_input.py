import copy,json,unittest
from metaculus_agent import decision_input,decision_model,evidence_packing
from ForecastAgent.competition import mercury
from test_evidence_packing import source

class CompactInputTests(unittest.TestCase):
    def test_rules_options_and_units_survive_compaction_without_mutating_input(self):
        q=dict(question='Example',resolution_criteria='Exact rule',fine_print='Exception',background='Observed background',options=['a','b'],unit='millions',scaling=dict(continuous_range=list(range(201)),range_min=0,range_max=200),pipeline='runtime',original_evidence_reading={'metadata':'large'})
        before=copy.deepcopy(q);out=decision_input.question_view(q)
        for key in ['resolution_criteria','fine_print','background','options','unit']:self.assertEqual(out[key],q[key])
        self.assertNotIn('continuous_range',out['scaling']);self.assertNotIn('pipeline',out);self.assertEqual(q,before)
    def test_native_numeric_boundaries_and_bin_ids_are_preserved(self):
        spec=dict(kind='numeric',criteria={'below':'Resolving value strictly below -5.','bin_0':'Resolving value at least -5 and strictly less than 0 in the question units.','bin_1':'Resolving value at least 0 and less than or equal to 12.5 in the question units.'})
        before=copy.deepcopy(spec);r=decision_input.registry('numeric',spec)['event_outcome']['criteria']
        self.assertEqual(r,{'below':spec['criteria']['below'],'bin_0':'[-5, 0)','bin_1':'[0, 12.5]'});self.assertEqual(spec,before)
    def test_wire_compaction_is_lossless_and_measurement_matches_actual_bytes(self):
        state={'question':{'question':'厄尔尼诺'},'sources':[{'source_id':'s','url':'https://example.org','body_sha256':'a'*64}],
               'evidence':[{'evidence_id':'e','source_id':'s','url':'https://example.org','body_sha256':'a'*64,'start':2,'end':5,'text':'原始文'}]}
        before=copy.deepcopy(state);wire=decision_model.request_payload(state,{'event_yes':{'type':'noul'}})
        self.assertNotIn('url',wire['state']['evidence'][0]);self.assertEqual(wire['state']['evidence'][0]['text'],'原始文')
        self.assertEqual(evidence_packing.measure(state,{'event_yes':{'type':'noul'}}),len(decision_model.encode(wire)))
        self.assertEqual(json.loads(decision_model.encode(wire)),wire);self.assertEqual(state,before)

if __name__=='__main__':unittest.main()
