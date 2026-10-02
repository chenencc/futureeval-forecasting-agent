"""Check direct decision input provenance and context bounds."""
import copy
import unittest

from ForecastAgent.analysis.super_direct_trial import compact


class DirectEvidenceTests(unittest.TestCase):
    def test_whole_spans_and_balanced_sources_survive(self):
        packet={'question':{'id':'1','question':'Will race-conscious districting be prohibited?',
                            'resolution_criteria':'Before 2027.'},
                'sources':[{'source_id':s,'url':'https://example.org/'+s,'body_sha256':s} for s in ['S1','S2']],
                'evidence':[{'evidence_id':f'E{i:04}','source_id':s,'text':'Race districting '+('x'*900),
                             'start':i*900,'end':i*900+900} for i,s in enumerate(['S1','S1','S1','S2','S2','S2'])],
                'acquisition_gaps':['Missing observation.']}
        original=copy.deepcopy(packet)
        state,audit=compact(packet,limit=4500)
        self.assertEqual(packet,original)
        self.assertEqual({x['source_id'] for x in state['evidence']},{'S1','S2'})
        for span in state['evidence']:self.assertIn(span,original['evidence'])
        self.assertTrue(audit['omitted_ids'])
        self.assertNotIn('analysis',state)
        self.assertNotIn('resolution',state)


if __name__=='__main__':unittest.main()
