"""Exact body selection replaces model transcription without weakening evidence checks."""
from copy import deepcopy
import hashlib,unittest
from pipeline_v2.review_citations import citation_choices,bind_review_citation
from pipeline_v2.acquisition import review_pending_materials,review_schema
from pipeline_v2.admission import assess
from test_pipeline_v2_admission import source,proposal,TEXT,CUT

D=dict(title='Will Target revenue exceed 2600?',resolution_criteria='Use issuer report.')


class ReviewCitationTests(unittest.TestCase):
    def material(self,sid='s1',text=TEXT):return dict(source_id=sid,text=text,citation_choices=citation_choices(sid,text,D))
    def test_choice_is_body_bound_literal_and_deterministic(self):
        m=self.material();c=m['citation_choices'][0]
        self.assertEqual(m['citation_choices'],self.material()['citation_choices'])
        self.assertEqual(TEXT[c['span']['start']:c['span']['end']],c['quote'])
        self.assertEqual(c['body_sha256'],hashlib.sha256(TEXT.encode()).hexdigest())
        p=proposal();p['relevance'].update(citation_id=c['citation_id'],quote='Wrong model transcription that should not be used.')
        bound,error=bind_review_citation(p,m);self.assertIsNone(error)
        self.assertEqual(bound['relevance']['quote'],c['quote'])
        self.assertNotIn('citation_id',bound['relevance']);self.assertIn('citation_id',p['relevance'])
        b=assess(source(),TEXT,bound,cutoff=CUT);self.assertEqual(b['inclusion'],'core')
        self.assertEqual(assess(source(),TEXT,b,cutoff=CUT,stored=True,require_kind=True),b)
    def test_unknown_cross_source_or_changed_body_is_not_bound(self):
        m=self.material();ident=m['citation_choices'][0]['citation_id'];p=proposal();p['relevance']=dict(p['relevance'],citation_id=ident)
        for other,expected in [(self.material('s2'),'REVIEW_CITATION_UNBOUND'),(dict(m,text=TEXT+' changed'),'REVIEW_CITATION_BODY_CHANGED')]:
            self.assertEqual(bind_review_citation(p,other)[1],expected)
        q=deepcopy(p);q['relevance']['citation_id']='invented';self.assertEqual(bind_review_citation(q,m)[1],'REVIEW_CITATION_UNBOUND')
    def test_null_reference_does_not_promote_and_hard_filters_still_apply(self):
        m=self.material();p=proposal();p['relevance']['citation_id']=None
        bound,error=bind_review_citation(p,m);self.assertIsNone(error)
        self.assertEqual(assess(source(),TEXT,bound,cutoff=CUT)['inclusion'],'pending')
        p['relevance']['citation_id']=m['citation_choices'][0]['citation_id'];bound,_=bind_review_citation(p,m)
        for kind in ['market_probability','irrelevant','reference_index']:
            q=dict(bound,content_kind=kind);self.assertEqual(assess(source(),TEXT,q,cutoff=CUT)['inclusion'],'excluded')
        q=deepcopy(bound);q['relevance']['as_of_date']='2099-01-01';self.assertEqual(assess(source(),TEXT,q,cutoff=CUT)['inclusion'],'pending')
    def test_schema_and_native_review_bind_id_without_another_request(self):
        s=source();s['text_sha256']=hashlib.sha256(TEXT.encode()).hexdigest()
        current=dict(evidence=dict(sources=[s],texts={s['text_sha256']:TEXT}));materials=[assess(s,TEXT)];gaps=[];calls=[]
        def reviewer(state):
            calls.append(state);schema=review_schema(state);props=schema['properties']['materials']['items']['properties']['relevance']['properties']
            self.assertIn('citation_id',props);self.assertNotIn('quote',props)
            ident=state['fetched_materials'][0]['citation_choices'][0]['citation_id']
            p=proposal();p['relevance'].pop('quote');p['relevance']['citation_id']=ident
            return dict(action='finish',materials=[p])
        review_pending_materials(current,materials,D,reviewer,gaps,cutoff=CUT)
        self.assertEqual(len(calls),1);self.assertEqual(materials[0]['inclusion'],'core');self.assertEqual(gaps,[])
    def test_bad_id_remains_pending_with_a_visible_gap(self):
        s=source();s['text_sha256']=hashlib.sha256(TEXT.encode()).hexdigest()
        current=dict(evidence=dict(sources=[s],texts={s['text_sha256']:TEXT}));materials=[assess(s,TEXT)];gaps=[]
        p=proposal();p['relevance']['citation_id']='invented'
        review_pending_materials(current,materials,D,lambda _:dict(action='finish',materials=[p]),gaps,cutoff=CUT)
        self.assertEqual(materials[0]['inclusion'],'pending');self.assertEqual(gaps[0]['code'],'REVIEW_CITATION_UNBOUND')
    def test_full_body_is_unchanged_and_catalog_is_bounded(self):
        text=(TEXT+'\n')*100;choices=citation_choices('s1',text,D)
        self.assertLessEqual(len(choices),24)
        self.assertTrue(all(12<=len(c['quote'])<=1000 and text[c['span']['start']:c['span']['end']]==c['quote'] for c in choices))
    def test_flat_multi_body_schema_keeps_all_fields_and_program_rejects_cross_source(self):
        from jsonschema import Draft202012Validator,ValidationError
        rows=[self.material('s1'),self.material('s2')]
        schema=review_schema(dict(fetched_materials=rows));p=proposal(forecast_relevance='direct')
        p['relevance'].pop('quote');p['relevance']['citation_id']=rows[0]['citation_choices'][0]['citation_id']
        q=deepcopy(p);q['source_id']='s2';q['relevance']['citation_id']=rows[1]['citation_choices'][0]['citation_id']
        action=dict(action='finish',materials=[p,q]);Draft202012Validator(schema).validate(action)
        self.assertNotIn('oneOf',schema['properties']['materials']['items'])
        with self.assertRaises(ValidationError):Draft202012Validator(schema).validate(dict(action='finish',materials=[p]))
        p['relevance']['citation_id']=rows[1]['citation_choices'][0]['citation_id']
        Draft202012Validator(schema).validate(action)
        self.assertEqual(bind_review_citation(p,rows[0])[1],'REVIEW_CITATION_UNBOUND')
        fragment=dict(action='finish',materials=[dict(source_id=m['source_id'],relevance=dict(citation_id=m['citation_choices'][0]['citation_id'])) for m in rows])
        with self.assertRaises(ValidationError):Draft202012Validator(schema).validate(fragment)
    def test_integer_reference_binds_identical_hash_and_offsets_without_transcription(self):
        m=self.material();p=proposal();choice=m['citation_choices'][-1]
        p['relevance']['citation_index']=choice['citation_index']
        bound,error=bind_review_citation(p,m);self.assertIsNone(error)
        self.assertEqual(bound['relevance']['span'],choice['span'])
        self.assertEqual(bound['relevance']['quote'],choice['quote'])
        for bad in [True,'1',-1,999999]:
            p['relevance']['citation_index']=bad;self.assertEqual(bind_review_citation(p,m)[1],'REVIEW_CITATION_UNBOUND')
    def test_conflicting_id_and_index_or_changed_body_never_bind(self):
        m=self.material();p=proposal();p['relevance'].update(citation_index=1,citation_id='foreign')
        self.assertEqual(bind_review_citation(p,m)[1],'REVIEW_CITATION_CONFLICT')
        p['relevance'].pop('citation_id')
        self.assertEqual(bind_review_citation(p,dict(m,text=TEXT+' changed'))[1],'REVIEW_CITATION_BODY_CHANGED')
    def test_integer_wire_contract_has_no_hash_or_quote_output_requirement(self):
        from pipeline_v2.acquisition import review_system
        state=dict(citation_reference='index',fetched_materials=[dict(source_id='s1',text=TEXT,citation_choices=[dict(citation_index=1,quote=TEXT)])])
        schema=review_schema(state);props=schema['properties']['materials']['items']['properties']['relevance']['properties']
        self.assertIn('citation_index',props);self.assertNotIn('citation_id',props);self.assertNotIn('quote',props)
        prompt=review_system(state)
        self.assertIn('"citation_index":1',prompt);self.assertNotIn('"quote":',prompt);self.assertNotIn('"citation_id":',prompt)
    def test_selected_offsets_remain_exact_when_the_same_passage_occurs_twice(self):
        text=TEXT+'\n'+TEXT
        s=source();s['text_sha256']=hashlib.sha256(text.encode()).hexdigest()
        current=dict(evidence=dict(sources=[s],texts={s['text_sha256']:text}));materials=[assess(s,text)];gaps=[];selected=[]
        def reviewer(state):
            choices=state['fetched_materials'][0]['citation_choices'];c=choices[-1];selected.append(c)
            p=proposal();p['relevance']['citation_id']=c['citation_id'];return dict(action='finish',materials=[p])
        review_pending_materials(current,materials,D,reviewer,gaps,cutoff=CUT)
        self.assertEqual(materials[0]['relevance']['span'],selected[0]['span'])


if __name__=='__main__':unittest.main()
