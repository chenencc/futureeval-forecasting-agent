import copy,os,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from metaculus_agent import decision_relevance as r,decision_canonical as c,decision_analysis as analysis,decision_model as model,evidence_packing as packing
from ForecastAgent.competition import mercury
from test_evidence_packing import source
from test_decision_model import response

class RelevanceTests(unittest.TestCase):
 def fixture(self):
  q={'question':'Will Example Alpha approve the measure?','resolution_criteria':'Approval by the board before the deadline.'}
  body='This community archive catalogue records blue storage folders and paper binding supplies.'
  doc=dict(id='a',text=body,complete=True)
  review=dict(id='a',relation='irrelevant',entity='mismatch',metric='mismatch',causal_relevance='no',quote=body,reason='Unrelated storage inventory.')
  return q,doc,review
 def test_exact_full_body_irrelevance_is_bound(self):
  q,d,x=self.fixture();v=r.bind(q,d,x);self.assertEqual(v['decision'],'exclude')
  self.assertEqual(r.exclusion(q,'https://example.org/inventory',{'content':d['text']},v),'full_document_model_review_irrelevant_bound_quote')
 def test_unknown_partial_invalid_quote_or_driver_kept(self):
  q,d,x=self.fixture()
  for field,value in [('relation','uncertain'),('entity','unknown'),('metric','unknown'),('causal_relevance','yes'),('relation','driver'),('relation','baseline'),('quote','Invented quote')]:
   v=copy.deepcopy(x);v[field]=value;self.assertEqual(r.bind(q,d,v)['decision'],'retain')
  self.assertEqual(r.bind(q,{**d,'complete':False},x)['decision'],'retain')
 def test_primary_source_and_rule_or_body_changes_kept(self):
  q,d,x=self.fixture();q['resolution_criteria']+=' https://example.org/inventory';v=r.bind(q,d,x)
  self.assertIsNone(r.exclusion(q,'https://example.org/inventory',{'content':d['text']},v))
  self.assertIsNone(r.exclusion({**q,'question':'Other question'},'https://different.org',{'content':d['text']},v))
  self.assertIsNone(r.exclusion(q,'https://different.org',{'content':d['text']+' changed'},v))
 def test_relevant_counterevidence_never_becomes_exclusion(self):
  q,d,x=self.fixture();d['text']='Example Alpha board expressly rejected the measure before the deadline, contradicting approval.'
  x.update(relation='direct',entity='match',metric='match',causal_relevance='yes',quote=d['text'])
  self.assertEqual(r.bind(q,d,x)['decision'],'retain')
 def test_tampered_receipt_not_used(self):
  q,d,x=self.fixture();v=r.bind(q,d,x);v['quote_span']['start']=2
  self.assertIsNone(r.exclusion(q,'https://example.org',{'content':d['text']},v))
 def test_optional_review_exclusion_cannot_override_baseline(self):
  q,d,x=self.fixture();raw=source(d['text'],binding=False);p=packing.prepare(mercury.packet_for(raw),raw);url=next(iter(raw['pages']))
  raw['analysis_selection_reviews']={url:r.bind(p['question'],d,x)}
  self.assertIsNotNone(c.build_context(p,raw)['sources'][0]['exclusion'])
  raw['source_bindings']={url:dict(forecast_relevance='baseline')}
  self.assertIsNone(c.build_context(p,raw)['sources'][0]['exclusion'])
 def test_integrated_analysis_saves_receipt_and_never_sends_private_context(self):
  raw=source('Example Alpha board did NOT approve the measure before the deadline.\n',binding=False)
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'METACULUS_DECISION_INPUT_PROFILE':'canonical-v3','OPENROUTER_API_KEY':'fixture-only'}):
   def fake(state,registry,*args):
    wire=model.request_payload(state,registry);self.assertNotIn(c.CONTEXT,wire['state']);self.assertIn('NOT approve',json.dumps(wire));return response(registry)
   with patch.object(model,'decide',side_effect=fake):analysis.run(raw,Path(tmp),dict(first_byte_limit=9830,second_byte_limit=12943),lambda:None)
   self.assertTrue((Path(tmp)/'final/normalization-audit.json').exists())

if __name__=='__main__':unittest.main()
