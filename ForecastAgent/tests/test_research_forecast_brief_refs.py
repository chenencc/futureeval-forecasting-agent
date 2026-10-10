"""Reference integrity, error isolation and bounded prospective replay."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.research_loop import forecast_brief_refs as refs, forecast_brief as legacy, brief_trial, target_pack
from ForecastAgent.tests.test_research_forecast_brief import table_bundle
from ForecastAgent.tests.test_competition_mercury import response


def common():
    bundle=table_bundle();heads,_=legacy.registry(bundle['request'])
    return target_pack.pack(bundle,heads)[0]


def proposal(state):
    index=refs.catalog(state)
    row=next(r for r in index['references'] if '| Alice Example | 1 | 202.0 |' in r['text'])
    header=next(r for r in index['references'] if '| Player | Games | Passing yards |' in r['text'])
    return {'facts':[{'id':'F1','role':'baseline','evidence_ids':[row['reference_id'],header['reference_id']],
        'interpretation':'Captured projection for the player; not an observed target-game result.',
        'limitation':'The applicable game window is unknown.'}],
        'change_factors':[{'fact_ids':['F1'],'direction':'unknown',
            'mechanism':'Variance and opponent affect the upper tail.','condition':'Comparable target conditions are unknown.'}],
        'forecast_frame':'Use the projection as limited context; the mean alone does not identify the tail probability.',
        'uncertainties':['Applicable game window and variance are unavailable.']}


class ReferenceBriefTests(unittest.TestCase):
    def test_program_binds_full_markdown_row_and_headers_without_model_quotes(self):
        state=common();before=copy.deepcopy(state);accepted,audit=refs.validate_brief(proposal(state),state)
        self.assertEqual(state,before)
        self.assertTrue(any(r['text']=='| Alice Example | 1 | 202.0 |\n' for r in audit['bound_originals']))
        self.assertTrue(any('| Player | Games | Passing yards |' in r['text'] for r in audit['bound_originals']))
        self.assertFalse(audit['meaning_verified']);self.assertFalse(audit['model_generated_dates_or_units'])
        self.assertNotIn('quote',accepted['facts'][0]);self.assertNotIn('period',accepted['facts'][0])

    def test_markdown_linebreaks_and_literal_tokens_are_preserved_not_semantically_assigned(self):
        state=common();e=state['evidence'][0]
        e['text']='# October 6, 2026\r\n\r\n[MHHW](https://example.org/units) = 1.624 meters\r\n'
        e['end']=e['start']+len(e['text'])
        index=refs.catalog(state)
        chosen=next(r for r in index['references'] if 'MHHW' in r['text'])
        p=proposal(common());p['facts'][0]['evidence_ids']=[chosen['reference_id']]
        _,audit=refs.validate_brief(p,state)
        bound=next(r for r in audit['bound_originals'] if r['reference_id']==chosen['reference_id'])
        self.assertEqual(bound['text'],chosen['text']);self.assertIn('\r\n',bound['text'])
        self.assertIn('meters',bound['literal_unit_tokens']);self.assertEqual(bound['event_period_status'],'not_semantically_verified')
        self.assertEqual(bound['literal_date_tokens'],[])

    def test_every_bound_slice_is_exact_visible_parent_text_and_catalog_is_deterministic(self):
        state=common();index=refs.catalog(state)
        self.assertEqual(index,refs.catalog(copy.deepcopy(state)))
        originals={e['evidence_id']:e for e in state['evidence']}
        for r in index['references']:
            e=originals[r['evidence_id']]
            self.assertEqual(r['text'],e['text'][r['start']-e['start']:r['end']-e['start']])
        self.assertFalse(index['new_source_text']);self.assertFalse(index['source_text_selection_changed'])

    def test_headers_are_not_inferred_across_unseen_source_gaps(self):
        index=refs.catalog(common())
        row=next(r for r in index['references'] if '| Alice Example | 1 | 202.0 |' in r['text'])
        self.assertEqual(row['context_ids'],[])
        contiguous=next(r for r in index['references'] if '| Other Player 0 | 1 | 500 |' in r['text'])
        self.assertTrue(contiguous['context_ids'])

    def test_hidden_id_duplicate_ids_and_model_supplied_quotes_cannot_pass(self):
        state=common()
        for change in ({'evidence_ids':['R9999']},{'evidence_ids':['R0001','R0001']},{'quote':'A fabricated outcome'}):
            with self.subTest(change=change):
                p=proposal(state);p['facts'][0].update(change)
                with self.assertRaises(ValueError):refs.validate_brief(p,state)

    def test_bad_fact_and_dependents_are_isolated_without_repairing_entity_or_claim(self):
        state=common();p=proposal(state)
        p['facts'].append({**p['facts'][0],'id':'bad','evidence_ids':['R9999']})
        p['change_factors'][0]['fact_ids']=['bad']
        accepted,audit=refs.validate_brief(p,state)
        self.assertEqual([f['id'] for f in accepted['facts']],['F1'])
        self.assertEqual(accepted['change_factors'],[]);self.assertIn('rejected',accepted['forecast_frame'])
        self.assertEqual(len(audit['rejected']),2)

    def test_facts_string_is_not_reinterpreted_as_evidence(self):
        state=common();p=proposal(state);p['facts']=json.dumps(p['facts'])
        with self.assertRaisesRegex(ValueError,'bounded array'):refs.validate_brief(p,state)

    def test_inferred_date_cannot_be_inserted_as_a_bound_field(self):
        state=common();p=proposal(state);p['facts'][0]['period']='October 15, 2026'
        with self.assertRaises(ValueError):refs.validate_brief(p,state)

    def test_ambiguous_parent_and_bad_offsets_are_rejected(self):
        state=common();state['evidence'].append(copy.deepcopy(state['evidence'][0]))
        with self.assertRaises(ValueError):refs.catalog(state)
        state=common();state['evidence'][0]['start']+=1
        with self.assertRaises(ValueError):refs.catalog(state)

    def test_source_display_contains_no_hidden_or_new_text(self):
        state=common();index=refs.catalog(state);view=refs.model_view(state,index)
        self.assertEqual(view['question'],state['question']);self.assertEqual(view['sources'],state['sources'])
        for r in view['evidence']:
            self.assertTrue(any(r['text'] in e['text'] and e['source_id']==r['source_id'] for e in state['evidence']))
        tool=refs.tool_for(index);ids=tool['function']['parameters']['properties']['facts']['items']['properties']['evidence_ids']['items']['enum']
        self.assertEqual(ids,[r['reference_id'] for r in index['references']])

    def test_scoring_keeps_exact_common_evidence_registry_and_probability_conversion(self):
        state=common();bundle=table_bundle();heads,spec=legacy.registry(bundle['request'])
        accepted,_=refs.validate_brief(proposal(state),state)
        with tempfile.TemporaryDirectory() as tmp,patch.object(refs.decision_http,'call',return_value=response(heads)) as call:
            result=refs.score(bundle['request'],state,heads,spec,Path(tmp),accepted)
        sent=call.call_args.args[0]
        self.assertTrue(all(sent[k]==v for k,v in state.items()))
        self.assertEqual(call.call_args.args[2],heads);self.assertEqual(result['payload']['probability_yes'],.98)
        self.assertTrue(sent['forecast_brief']['reference_bindings'])

    def test_cached_reference_trial_no_extra_calls_no_quota_reset(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}):
            parent=Path(tmp)/'input.json';save(parent,table_bundle());root=Path(tmp)/'trial'
            def super_call(messages,key,**kwargs):
                # Recover the frozen common state, not a invented source.
                state=target_pack.pack(load(parent),legacy.registry(load(parent)['request'])[0])[0]
                r={'request':{'model':refs.SUPER},'status':'reserved'};t=kwargs['observer']('reserve',r)
                message={'tool_calls':[{'function':{'name':'record_forecast_brief','arguments':json.dumps(proposal(state))}}]}
                r.update(status='received',response={'usage':{'prompt_tokens':10,'completion_tokens':10}})
                kwargs['observer']('complete',r,t);return message
            def mercury(state,heads,key,observer,**kwargs):
                r={'status':'reserved','request':{'model':'mercury'}};t=observer('reserve',r)
                answer=response(heads);r.update(status='received',response=answer);observer('complete',r,t);return answer
            with patch.object(refs,'ask_model',side_effect=super_call),patch.object(refs.decision_http,'decide',side_effect=mercury):
                first=brief_trial.run([parent],root,execute=True,max_http=3,brief_protocol='references')
            self.assertEqual(first['paired_completed'],1);self.assertEqual(first['new_http'],3)
            with patch.object(refs,'ask_model',side_effect=AssertionError('Unexpected Super HTTP')),patch.object(refs.decision_http,'decide',side_effect=AssertionError('Unexpected Mercury HTTP')):
                second=brief_trial.run([parent],root,execute=True,max_http=3,brief_protocol='references')
            self.assertEqual(first,second)
            with self.assertRaises(ValueError):brief_trial.run([parent],root,execute=True,max_http=4,brief_protocol='references')
            with self.assertRaises(ValueError):brief_trial.run([parent],root,execute=True,max_http=3,brief_protocol='literal')


if __name__=='__main__':unittest.main()
