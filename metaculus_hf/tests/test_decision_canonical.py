import copy,os,unittest
from unittest.mock import patch
from metaculus_agent import decision_canonical as canonical,decision_model as model,evidence_packing as packing
from ForecastAgent.competition import mercury
from ForecastAgent.analysis import mercury_evidence_chain as spans
from test_evidence_packing import source

class CanonicalTests(unittest.TestCase):
 def setUp(self):
  env=patch.dict(os.environ,{'METACULUS_DECISION_INPUT_PROFILE':'canonical-v3'});env.start();self.addCleanup(env.stop)
 def fixture(self):
  raw=source('Example Alpha first observation is 9.\nExample Alpha correction: NOT 9, but 8.\n',binding=False)
  packet=packing.prepare(mercury.packet_for(raw),raw);state=spans.initial_state(packet)
  state[canonical.CONTEXT]=canonical.build_context(packet,raw)
  state,_=packing.select(packet,state,20000,spans.questions())
  return raw,packet,state
 def wire(self,s):return model.encode(model.request_payload(s,spans.questions()))
 def test_order_labels_and_metadata_are_canonical(self):
  _,_,s=self.fixture();expected=self.wire(s);changed=copy.deepcopy(s)
  changed['evidence'].reverse();changed['sources'].reverse()
  ids={x['source_id']:'renamed-'+str(i) for i,x in enumerate(changed['sources'])}
  for x in changed['sources']:
   x['source_id']=ids[x['source_id']];x.pop('body_sha256',None)
  for x in changed['evidence']:
   x['source_id']=ids[x['source_id']]
   for k in ('evidence_id','start','end','url','body_sha256'):x.pop(k,None)
  changed={k:changed[k] for k in reversed(list(changed))}
  self.assertEqual(self.wire(changed),expected)
 def test_outside_capture_is_excluded_and_audited_without_changing_probability_input(self):
  _,_,s=self.fixture();changed=copy.deepcopy(s)
  changed['sources'].append(dict(source_id='other',url='https://unseen.example.org'))
  changed['evidence'].insert(0,dict(source_id='other',text='Unbound but plausible claim'))
  self.assertEqual(self.wire(changed),self.wire(s));audit=canonical.render(changed,{})[2]
  self.assertEqual(audit['excluded'][0]['reason'],'source_outside_frozen_capture')
 def test_capture_included_unreviewed_noise_is_not_silently_removed(self):
  raw=source('Example Alpha first observation.\nUnrelated blue folders and green paper.\n',binding=False)
  p=packing.prepare(mercury.packet_for(raw),raw);s=spans.initial_state(p);s[canonical.CONTEXT]=canonical.build_context(p,raw)
  s['evidence']=[p['evidence'][0]];s['sources']=p['sources']
  self.assertIn(b'green paper',self.wire(s))
 def test_counterevidence_original_text_and_snapshot_not_changed(self):
  raw,_,s=self.fixture();before=copy.deepcopy(s);wire=self.wire(s)
  self.assertIn(b'NOT 9, but 8',wire);self.assertEqual(s,before)
 def test_changed_known_source_text_fails(self):
  _,_,s=self.fixture();s['evidence'][0]['text']+=' altered'
  with self.assertRaisesRegex(ValueError,'UNBOUND_TEXT'):self.wire(s)
 def test_changed_offsets_and_hash_fail(self):
  _,_,s=self.fixture();s['evidence'][0]['start']+=1
  with self.assertRaisesRegex(ValueError,'SPAN_CHANGED'):self.wire(s)
  _,_,s=self.fixture();s['sources'][0]['body_sha256']='bad'
  with self.assertRaisesRegex(ValueError,'BODY_HASH_CHANGED'):self.wire(s)
 def test_missing_context_and_changed_question_fail(self):
  _,_,s=self.fixture();s.pop(canonical.CONTEXT)
  with self.assertRaisesRegex(ValueError,'CONTEXT_REQUIRED'):self.wire(s)
  _,_,s=self.fixture();s['question']['resolution_criteria']='different rule'
  with self.assertRaisesRegex(ValueError,'QUESTION_CHANGED'):self.wire(s)
 def test_context_tampering_fails(self):
  _,_,s=self.fixture();s[canonical.CONTEXT]['sources'][0]['url']='different'
  with self.assertRaisesRegex(ValueError,'CONTEXT_HASH_MISMATCH'):self.wire(s)
 def test_reviewed_dual_mismatch_required_for_exclusion(self):
  raw=source('Unrelated inventory of blue folders.\n',binding=False);url=next(iter(raw['pages']))
  raw['source_bindings']={url:dict(source_id='s1',inclusion='excluded',forecast_relevance='none',relevance=dict(entity='mismatch',metric='mismatch',span=dict(start=0,end=12)))}
  p=packing.prepare(mercury.packet_for(raw),raw)
  self.assertIsNone(canonical.build_context(p,raw)['sources'][0]['exclusion'])
  raw['original_reading']['reviewed_source_ids']=['s1']
  ctx=canonical.build_context(p,raw);self.assertEqual(ctx['sources'][0]['exclusion'],'reviewed_entity_and_metric_mismatch')
  s=spans.initial_state(p);s[canonical.CONTEXT]=ctx
  with self.assertRaisesRegex(ValueError,'NO_ADMITTED_EVIDENCE'):packing.select(p,s,20000,spans.questions())
  raw['source_bindings'][url]['forecast_relevance']='driver'
  self.assertIsNone(canonical.build_context(p,raw)['sources'][0]['exclusion'])
 def test_ambiguous_missing_offsets_are_not_guessed(self):
  raw=source('same\nsame\n',binding=False);p=packing.prepare(mercury.packet_for(raw),raw)
  item=p['sources'][0];p['evidence']=[packing._span(item,raw['pages'][item['url']]['content'],0,5),packing._span(item,raw['pages'][item['url']]['content'],5,10)]
  s=spans.initial_state(p);s[canonical.CONTEXT]=canonical.build_context(p,raw);s['sources']=p['sources'];s['evidence']=[dict(source_id=item['source_id'],text='same\n')]
  with self.assertRaisesRegex(ValueError,'AMBIGUOUS_SPAN'):self.wire(s)
 def test_list_order_is_not_changed(self):
  value={'b':1,'a':{'options':['third','first','second']}}
  self.assertEqual(canonical.ordered(value)['a']['options'],value['a']['options'])

if __name__=='__main__':unittest.main()
