"""Ownership and failure semantics for typed assertion audit experiments."""
import copy
import unittest
from ForecastAgent.research_loop import assertion_audit as audit

class AssertionAuditTests(unittest.TestCase):
    def setUp(self):
        self.sources=[{'evidence_id':'R1','text':'Published Wednesday. Classes close Thursday.'},
                      {'evidence_id':'R2','text':'Unrelated article.'}]
        self.claims=[{'id':'C1','text':'Classes close Wednesday.','reference_ids':['R1'],
            'qualifiers':[{'aspect':'fact','text':'Classes close.'},
                          {'aspect':'time','text':'The closure day is Wednesday.'}]}]

    def prep(self,mode='atomic'):
        return audit.prepare({'question':'Will classes close?'},'2026-10-11',self.sources,self.claims,mode=mode)

    def answer(self,prepared,choices):
        return {'answers':{key:{'type':'choice','choice':choice,
            'probabilities':{k:float(k==choice) for k in audit.CHOICES}} for key,choice in zip(prepared['questions'],choices)}}

    def test_exact_originals_and_own_scope(self):
        before=copy.deepcopy(self.sources);p=self.prep()
        self.assertEqual(p['state']['originals'],before)
        self.assertTrue(all(h['own_reference_ids']==['R1'] for h in p['state']['fallible_hypotheses'].values()))
        self.assertEqual(self.sources,before)
        self.assertNotIn('Classes close Wednesday.',str(p['state']['fallible_hypotheses']))

    def test_missing_reference_rejected_before_request(self):
        self.claims[0]['reference_ids']=['absent']
        with self.assertRaises(ValueError):self.prep()

    def test_duplicate_reference_and_assertion_rejected(self):
        self.sources.append(copy.deepcopy(self.sources[0]))
        with self.assertRaises(ValueError):self.prep()
        self.sources.pop();self.claims.append(copy.deepcopy(self.claims[0]))
        with self.assertRaises(ValueError):self.prep()

    def test_invalid_or_empty_qualifiers_rejected(self):
        self.claims[0]['qualifiers']=[]
        with self.assertRaises(ValueError):self.prep('whole')
        self.claims[0]['qualifiers']=[{'aspect':'future_truth','text':'Known outcome'}]
        with self.assertRaises(ValueError):self.prep()

    def test_unsupported_date_cannot_inherit_supported_fact(self):
        p=self.prep();r=audit.summarize(p,self.answer(p,['supported','insufficient']))
        self.assertEqual(r['rows'][0]['disposition'],'insufficient')
        self.assertFalse(r['rows'][0]['meaning_verified'])
        self.assertFalse(r['graph_mutated'])

    def test_missing_answer_is_unreviewed(self):
        p=self.prep();r=audit.summarize(p,self.answer(p,['supported']))
        self.assertEqual(r['rows'][0]['disposition'],'unreviewed')

    def test_low_confidence_and_incoherent_choice_never_supported(self):
        p=self.prep('whole');response=self.answer(p,['supported']);a=next(iter(response['answers'].values()))
        a['probabilities']={'supported':0.6,'contradicted':0.1,'insufficient':0.3}
        self.assertEqual(audit.summarize(p,response)['rows'][0]['disposition'],'unreviewed')
        a['probabilities']={'supported':0.1,'contradicted':0.,'insufficient':0.9}
        self.assertEqual(audit.summarize(p,response)['rows'][0]['disposition'],'unreviewed')

    def test_truth_remains_unverified_when_every_part_supported(self):
        p=self.prep();r=audit.summarize(p,self.answer(p,['supported','supported']))
        self.assertEqual(r['rows'][0]['disposition'],'candidate_supported')
        self.assertFalse(r['decomposition_completeness_verified'])
        self.assertFalse(r['threshold_calibrated'])

    def test_whole_control_uses_single_head_without_atoms(self):
        p=self.prep('whole');self.assertEqual(len(p['questions']),1)
        self.assertEqual(next(iter(p['state']['fallible_hypotheses'].values()))['text'],self.claims[0]['text'])

    def test_nonfinite_distribution_and_threshold_rejected(self):
        p=self.prep('whole');r=self.answer(p,['supported']);next(iter(r['answers'].values()))['probabilities']['supported']=float('nan')
        self.assertEqual(audit.summarize(p,r)['rows'][0]['disposition'],'unreviewed')
        with self.assertRaises(ValueError):audit.summarize(p,r,threshold=float('nan'))

    def test_malformed_answer_container_is_unreviewed(self):
        p=self.prep('whole')
        for response in ({'answers':[]}, {'answers':{next(iter(p['questions'])):None}}):
            self.assertEqual(audit.summarize(p,response)['rows'][0]['disposition'],'unreviewed')

    def test_nested_reference_and_nonobject_qualifier_rejected(self):
        self.claims[0]['reference_ids']=[['R1']]
        with self.assertRaises(ValueError):self.prep()
        self.claims[0]['reference_ids']=['R1'];self.claims[0]['qualifiers']=[None]
        with self.assertRaises(ValueError):self.prep()

    def test_overlong_decomposition_isolates_only_bad_sibling(self):
        original=[{'id':'C1'},{'id':'C2'}]
        q={'aspect':'fact','text':'A fact.'}
        answer={'assertions':[{'id':'C1','qualifiers':[q]*7},{'id':'C2','qualifiers':[q]}]}
        r=audit.bind_decomposition(original,answer,max_qualifiers=5)
        self.assertEqual(list(r['accepted']),['C2'])
        self.assertEqual(r['unreviewed_assertion_ids'],['C1'])
        self.assertFalse(r['truncation_or_enum_remapping'])
        self.assertEqual(len(answer['assertions'][0]['qualifiers']),7)

    def test_unknown_decomposition_aspect_not_silently_renamed(self):
        answer={'assertions':[{'id':'C1','qualifiers':[{'aspect':'period','text':'2026 season'}]}]}
        r=audit.bind_decomposition([{'id':'C1'}],answer)
        self.assertEqual(r['accepted'],{})
        self.assertEqual(r['additional_model_calls'],0)

    def test_duplicate_decomposition_isolates_every_duplicate(self):
        a={'id':'C1','qualifiers':[{'aspect':'time','text':'The event occurred in2026.'}]}
        r=audit.bind_decomposition([{'id':'C1'}],{'assertions':[a,copy.deepcopy(a)]})
        self.assertEqual(r['accepted'],{})
        self.assertEqual(len(r['rejected']),2)

    def test_missing_decomposition_is_unreviewed_not_supported(self):
        r=audit.bind_decomposition([{'id':'C1'}],{'assertions':[]})
        self.assertEqual(r['unreviewed_assertion_ids'],['C1'])
        self.assertFalse(r['decomposition_completeness_verified'])

if __name__=='__main__':unittest.main()
