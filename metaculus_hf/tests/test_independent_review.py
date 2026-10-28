import copy
import tempfile
import unittest
from pathlib import Path
from metaculus_agent import independent_review as r, review_jobs as jobs


def fixture():
    q=dict(id='1',question='What was Alpha digital revenue in Q3 2026?',resolution_criteria='Revenue in USD for Q3 2026.',forecast_probability=.9,first_review='secret judgment')
    body='Alpha reported digital revenue of USD 90 million for Q3 2026. This is the reported period, not a forecast.'
    doc=dict(id='a',url='https://example.org/report',text=body,body_sha256=r.sha(body),complete=True,retrieved_at='2026-10-28T00:00:00Z',previous_review='must not leak')
    claim=dict(citation_index=1,interpretation='Reported digital revenue was USD 90 million for Q3 2026.',support='supported',entity_literal='Alpha',metric_literal='digital revenue',value_literal='90',unit_literal='USD',period_literal='Q3 2026')
    row=dict(id='a',relation='direct',entity='match',metric='match',claims=[claim],gaps=[])
    return q,doc,dict(documents=[row])


class FakeRouter:
    def __init__(self, result=None, error=None):self.result=result;self.error=error;self.calls=0
    def chat(self,system,state,**kwargs):
        self.calls+=1;self.state=state;self.kwargs=kwargs
        if self.error:raise self.error
        return copy.deepcopy(self.result)


class IndependentReviewTests(unittest.TestCase):
    def result(self, mutate=None, first=None):
        q,d,x=fixture()
        if mutate:mutate(d,x)
        return r.assemble(r.prepare(q,[d]),x,first_reviews=first)['documents'][0]
    def test_blind_input_and_requested_model(self):
        q,d,x=fixture();f=FakeRouter(x);v=r.run(f,q,[d],first_reviews={'a':{'relation':'direct','note':'hidden'}})
        self.assertEqual(f.kwargs['model'],'nvidia/nemotron-3-ultra-550b-a55b:free');self.assertFalse(f.kwargs['json_mode'])
        self.assertNotIn('previous_review',f.state['documents'][0]);self.assertNotIn('first_review',f.state['question']);self.assertNotIn('forecast_probability',f.state['question'])
        self.assertFalse(v['first_review_used_in_model_prompt']);self.assertFalse(v['truth_verified'])
    def test_valid_bound_literal_evidence(self):
        d=self.result();self.assertEqual(d['grade'],'bound_evidence');self.assertEqual(d['claims'][0]['span']['start'],0)
    def test_wrong_period_cannot_be_bound(self):
        d=self.result(lambda d,x:x['documents'][0]['claims'][0].update(period_literal='Q3 2025'))
        self.assertEqual(d['claims'][0]['grade'],'unresolved');self.assertEqual(d['claims'][0]['literal_checks']['period_literal'],'literal_mismatch')
    def test_wrong_entity_metric_value_unit_each_rejected(self):
        for field in ('entity_literal','metric_literal','value_literal','unit_literal'):
            d=self.result(lambda d,x:x['documents'][0]['claims'][0].update({field:'INVENTED'}))
            self.assertEqual(d['claims'][0]['grade'],'unresolved')
    def test_missing_period_is_lead_not_bound(self):
        d=self.result(lambda d,x:x['documents'][0]['claims'][0].update(period_literal=None))
        self.assertEqual(d['claims'][0]['grade'],'background_or_lead')
    def test_wrong_citation_is_unresolved(self):
        d=self.result(lambda d,x:x['documents'][0]['claims'][0].update(citation_index=999))
        self.assertFalse(d['claims'][0]['quote_bound']);self.assertEqual(d['grade'],'unresolved')
    def test_partial_body_not_promoted(self):
        self.assertEqual(self.result(lambda d,x:d.update(complete=False))['grade'],'background_or_lead')
    def test_disagreement_downgrades_not_deletes(self):
        d=self.result(first={'a':{'forecast_relevance':'none'}})
        self.assertEqual(d['grade'],'unresolved');self.assertEqual(d['source_action'],'retain')
    def test_entity_disagreement_is_not_hidden_by_same_role(self):
        d=self.result(first={'a':{'forecast_relevance':'direct','relevance':{'entity':'mismatch'}}})
        self.assertTrue(d['first_review_disagreement']);self.assertEqual(d['grade'],'unresolved')
    def test_irrelevant_not_auto_deleted(self):
        d=self.result(lambda d,x:x['documents'][0].update(relation='irrelevant',claims=[]))
        self.assertEqual(d['grade'],'unresolved');self.assertEqual(d['source_action'],'retain')
    def test_counterevidence_is_retained(self):
        q,d,x=fixture();d['text']='Alpha did NOT approve the bill before 1 July 2026. This expressly contradicts approval by the deadline.';d['body_sha256']=r.sha(d['text'])
        x['documents'][0]['claims'][0].update(interpretation='Alpha did NOT approve the bill.',entity_literal='Alpha',metric_literal='approve the bill',value_literal=None,unit_literal=None,period_literal='1 July 2026')
        report=r.assemble(r.prepare(q,[d]),x);self.assertIn('NOT',report['documents'][0]['claims'][0]['quote'])
        self.assertEqual(report['documents'][0]['source_action'],'retain')
    def test_hash_and_size_bounds(self):
        q,d,x=fixture();d['text']+='changed'
        with self.assertRaisesRegex(ValueError,'HASH'):r.prepare(q,[d])
        d['text']='a'*90000;d['body_sha256']=r.sha(d['text'])
        with self.assertRaisesRegex(ValueError,'LIMIT'):r.prepare(q,[d])
    def test_schema_failures(self):
        q,d,x=fixture();state=r.prepare(q,[d])
        for result in ({},dict(documents=[]),dict(documents=[x['documents'][0]]*2)):
            with self.assertRaises(ValueError):r.assemble(state,result)
        x['documents'][0]['claims'][0]['citation_index']=True
        with self.assertRaises(ValueError):r.assemble(state,x)
    def test_unknown_metric_not_bound(self):
        d=self.result(lambda d,x:x['documents'][0].update(metric='unknown'))
        self.assertEqual(d['grade'],'background_or_lead')
    def test_unsupported_claim_never_promoted(self):
        d=self.result(lambda d,x:x['documents'][0]['claims'][0].update(support='contradicted'))
        self.assertEqual(d['grade'],'unresolved')


class ReviewJobTests(unittest.TestCase):
    def test_idempotent_enqueue_and_cache_no_provider_repeat(self):
        q,d,x=fixture();f=FakeRouter(x)
        with tempfile.TemporaryDirectory() as tmp:
            ident=jobs.enqueue(tmp,q,[d]);self.assertEqual(ident,jobs.enqueue(tmp,q,[d]));self.assertEqual(f.calls,0)
            first=jobs.run_one(tmp,ident,router=f);second=jobs.run_one(tmp,ident,router=f)
            self.assertEqual(first['status'],'reviewed');self.assertEqual(first,second);self.assertEqual(f.calls,1)
            self.assertTrue((Path(tmp)/ident/'review.parquet').exists())
    def test_error_retains_input_and_does_not_retry_automatically(self):
        q,d,x=fixture();f=FakeRouter(error=ValueError('MODEL_FORMAT'))
        with tempfile.TemporaryDirectory() as tmp:
            ident=jobs.enqueue(tmp,q,[d]);a=jobs.run_one(tmp,ident,router=f);b=jobs.run_one(tmp,ident,router=f)
            self.assertEqual(a['status'],'failed');self.assertEqual(a,b);self.assertEqual(f.calls,1)
            self.assertEqual(jobs.load(Path(tmp)/ident/'input.json')['state']['documents'][0]['text'],d['text'])
    def test_crash_marks_interrupted_without_call(self):
        q,d,x=fixture();f=FakeRouter(x)
        with tempfile.TemporaryDirectory() as tmp:
            ident=jobs.enqueue(tmp,q,[d]);path=Path(tmp)/ident/'job.json';job=jobs.load(path);job.update(status='running',attempts=1);jobs.save(path,job)
            self.assertEqual(jobs.run_one(tmp,ident,router=f)['status'],'interrupted');self.assertEqual(f.calls,0)
            self.assertEqual(jobs.run_one(tmp,ident,router=f,retry_failed=True)['status'],'reviewed');self.assertEqual(f.calls,1)
    def test_input_tamper_blocks_model(self):
        q,d,x=fixture();f=FakeRouter(x)
        with tempfile.TemporaryDirectory() as tmp:
            ident=jobs.enqueue(tmp,q,[d]);path=Path(tmp)/ident/'input.json';path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'INPUT_CHANGED'):jobs.run_one(tmp,ident,router=f)
            self.assertEqual(f.calls,0)
    def test_cache_tamper_blocks(self):
        q,d,x=fixture();f=FakeRouter(x)
        with tempfile.TemporaryDirectory() as tmp:
            ident=jobs.enqueue(tmp,q,[d]);jobs.run_one(tmp,ident,router=f)
            (Path(tmp)/ident/'review.parquet').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'CACHE_CHANGED'):jobs.run_one(tmp,ident,router=f)
            self.assertEqual(f.calls,1)
    def test_comparison_changes_cache_identity_not_prompt(self):
        q,d,x=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            a=jobs.enqueue(tmp,q,[d]);b=jobs.enqueue(tmp,q,[d],first_reviews={'a':{'relation':'irrelevant'}})
            self.assertNotEqual(a,b);self.assertEqual(jobs.load(Path(tmp)/a/'input.json')['state'],jobs.load(Path(tmp)/b/'input.json')['state'])


if __name__=='__main__':unittest.main()
