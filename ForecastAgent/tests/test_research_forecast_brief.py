"""Target rows, exact original bytes, failure isolation and durable paired scores."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import Journal, digest, load, save
from ForecastAgent.analysis.distributions import validate_cdf
from ForecastAgent.research_loop import target_pack as pack, forecast_brief as brief, brief_trial
from ForecastAgent.tests.test_research_loop import source_bundle
from ForecastAgent.tests.test_competition_mercury import response


def table_bundle(kind='binary'):
    b=source_bundle(kind)
    b['request']['question']='Will Alice Example exceed 250 passing yards in October 2026?'
    b['request']['resolution_criteria']='Use the official target game. A prior game is background only.'
    body='# Passing projections\n\nPeriod not specified in this captured table.\n\n'
    body+='| Player | Games | Passing yards |\n| --- | --- | --- |\n'
    body+=''.join(f'| Other Player {i} | 1 | 500 |\n' for i in range(300))
    body+='| Alice Example | 1 | 202.0 |\n'
    body+='\nPrior official projection is conditional on limited disruption.\n'
    b['pages']['https://example.org/report']['content']=body
    return b


def proposed(common):
    e=next(e for e in common['evidence'] if 'Alice Example | 1 | 202.0' in e['text'])
    return {'facts':[{'id':'baseline','role':'baseline','evidence_id':e['evidence_id'],
        'quote':'| Alice Example | 1 | 202.0 |','period':'','unit':'',
        'interpretation':'A captured projection for this player, not the realized target game.',
        'limitation':'Applicable week is not specified.'}],
        'change_factors':[{'fact_ids':['baseline'],'direction':'unknown',
            'mechanism':'Target variance and opponent can affect the upper tail.',
            'condition':'Comparable target game conditions are unknown.'}],
        'forecast_frame':'Use the projection as limited background. The upper-tail probability needs variance; do not infer it from a mean.',
        'uncertainties':['Applicable projection window and variance are unavailable.']}


class TargetPackingTests(unittest.TestCase):
    def test_late_target_row_headers_and_uncertain_window_survive_full_budget(self):
        b=table_bundle(); original=copy.deepcopy(b); heads,_=brief.registry(b['request'])
        s,a=pack.pack(b,heads,limit=6500)
        visible='\n'.join(e['text'] for e in s['evidence'])
        self.assertIn('| Alice Example | 1 | 202.0 |',visible)
        self.assertIn('| Player | Games | Passing yards |',visible)
        self.assertIn('Period not specified',visible)
        self.assertEqual(b,original);self.assertLessEqual(a['request_bytes'],6500)
        self.assertFalse(a['old_visible_passages_mandatory'])
        self.assertFalse(a['truth_verified'])

    def test_target_row_ranking_does_not_promote_other_rows_for_shared_headers(self):
        target=pack.profile({'question':'Will Alice Example exceed passing yards?',
                             'resolution_criteria':''})
        text='| Player | Passing yards |\n| Alice Example | 202 |\n| Wrong Person | 999 |\n'
        groups=[g for g in pack.groups(text) if g['kind']=='table_row']
        target_row=next(g for g in groups if 'Alice Example' in text[slice(*g['ranges'][-1])])
        other=next(g for g in groups if 'Wrong Person' in text[slice(*g['ranges'][-1])])
        self.assertGreater(pack.rank(text,target_row,target),pack.rank(text,other,target))

    def test_adjacent_markdown_labels_do_not_hide_an_exact_player_name(self):
        text='| [B. Example](https://example.org/1) QB DEN[Alice Example](https://example.org/1) QB DEN | 1 | 202.0 |'
        target=pack.profile({'question':'Will Alice Example exceed 250 passing yards?','resolution_criteria':''})
        group={'kind':'table_row','ranges':[(0,len(text))]}
        self.assertGreaterEqual(pack.rank(text,group,target)[0],20)

    def test_quantified_target_metric_precedes_directory_navigation(self):
        target=pack.profile({'question':'What global growth rate will the IMF project?','resolution_criteria':''})
        directory='World Economic Outlook archive provides global growth forecasts.'
        data='Global growth is projected to be 3.1 percent under a limited conflict assumption.'
        score=lambda text:pack.rank(text,{'kind':'prose','ranges':[(0,len(text))]},target)[0]
        self.assertGreater(score(data),score(directory))

    def test_primary_title_name_precedes_other_matchup_entities(self):
        target=pack.profile({'question':'Will Alice Example exceed passing yards against Other Team?','resolution_criteria':''})
        first='Alice Example passed for 214 yards in a prior game.'
        opponent='Other Team passing yards projection is 350.0 in an unrelated game.'
        score=lambda text:pack.rank(text,{'kind':'prose','ranges':[(0,len(text))]},target)[0]
        self.assertGreater(score(first),score(opponent))

    def test_source_coordinates_exact_and_no_request_metadata_used_for_rank(self):
        b=table_bundle();b['request']['acquisition_profile']='Wrong Person repeated forecast scores'
        h,_=brief.registry(b['request']);s,_=pack.pack(b,h)
        sources={x['source_id']:x for x in s['sources']}
        for e in s['evidence']:
            text=b['pages'][sources[e['source_id']]['url']]['content']
            self.assertEqual(e['text'],text[e['start']:e['end']])
        self.assertNotIn('acquisition_profile',json.dumps(pack.profile(s['question'])))

    def test_explicit_hash_mismatch_rejected(self):
        b=table_bundle();b['pages']['https://example.org/report']['content_sha256']='bad'
        heads,_=brief.registry(b['request'])
        with self.assertRaises(ValueError):pack.pack(b,heads)

    def test_incomplete_last_table_line_is_not_certified_complete(self):
        text='| Player | Yards |\n|---|---|\n| Alice Example | 202'
        rows=[g for g in pack.groups(text) if g['kind']=='table_row']
        self.assertFalse(rows[0]['table_structure_complete'])

    def test_registry_and_payload_work_for_all_five_question_types(self):
        for kind in ('binary','multiple_choice','numeric','date','discrete'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                b=table_bundle(kind);heads,spec=brief.registry(b['request']);s,_=pack.pack(b,heads)
                with patch.object(brief.decision_http,'call',return_value=response(heads)):
                    r=brief.score(b['request'],s,heads,spec,Path(tmp))
                if kind=='binary':self.assertEqual(r['payload']['probability_yes'],.98)
                elif kind=='multiple_choice':self.assertAlmostEqual(sum(r['payload']['probability_yes_per_category'].values()),1)
                else:validate_cdf(r['payload']['continuous_cdf'],b['request'],clipped=True)


class ForecastBriefTests(unittest.TestCase):
    def common(self):
        b=table_bundle();h,_=brief.registry(b['request']);return pack.pack(b,h)[0]

    def test_unsupported_annotation_isolated_without_dropping_literal_baseline(self):
        s=self.common();p=proposed(s);p['facts'][0]['period']='October 15, 2026'
        accepted,a=brief.validate_brief(p,s)
        self.assertEqual(accepted['facts'][0]['period'],'')
        self.assertEqual(accepted['facts'][0]['quote'],p['facts'][0]['quote'])
        self.assertEqual(len(a['isolated_annotations']),1);self.assertFalse(a['meaning_verified'])

    def test_bad_fact_drops_dependents_and_synthesis_but_retains_good_fact(self):
        s=self.common();p=proposed(s)
        p['facts'].append({**p['facts'][0],'id':'bad','quote':'Invented realized target outcome'})
        p['change_factors'][0]['fact_ids']=['bad']
        a,report=brief.validate_brief(p,s)
        self.assertEqual([f['id'] for f in a['facts']],['baseline']);self.assertFalse(a['change_factors'])
        self.assertIn('rejected',a['forecast_frame']);self.assertEqual(len(report['rejected']),2)

    def test_hidden_handle_or_quote_mismatch_with_no_facts_preserves_direct_fallback(self):
        s=self.common()
        for change in ({'evidence_id':'H9999'},{'quote':'Not in the selected source'}):
            p=proposed(s);p['facts'][0].update(change)
            with self.assertRaises(ValueError):brief.validate_brief(p,s)

    def test_same_visible_source_unique_literal_handle_repair_no_hidden_text(self):
        s=self.common();p=proposed(s)
        header=next(e for e in s['evidence'] if '| Player |' in e['text'] and p['facts'][0]['quote'] not in e['text'])
        p['facts'][0]['evidence_id']=header['evidence_id']
        accepted,a=brief.validate_brief(p,s)
        self.assertNotEqual(accepted['facts'][0]['evidence_id'],header['evidence_id'])
        self.assertEqual(len(a['binding_repairs']),1);self.assertFalse(a['binding_repairs'][0]['new_source_text'])
        s['evidence'].append({**header,'evidence_id':'H9998','source_id':'different',
            'text':'Not original text'})
        p['facts'][0]['evidence_id']='H9998'
        with self.assertRaises(ValueError):brief.validate_brief(p,s)

    def test_optional_string_uncertainties_do_not_discard_valid_baseline(self):
        s=self.common();p=proposed(s);p['uncertainties']='Serialized list rather than a list'
        accepted,a=brief.validate_brief(p,s)
        self.assertEqual(len(accepted['facts']),1);self.assertEqual(accepted['uncertainties'],[])
        self.assertTrue(any(r['section']=='uncertainties' for r in a['rejected']))

    def test_exact_common_state_preserved_and_event_probability_not_confidence_weighted(self):
        s=self.common();before=copy.deepcopy(s);b=table_bundle();h,spec=brief.registry(b['request'])
        accepted,_=brief.validate_brief(proposed(s),s)
        with tempfile.TemporaryDirectory() as tmp,patch.object(brief.decision_http,'call',return_value=response(h)) as call:
            r=brief.score(b['request'],s,h,spec,Path(tmp),accepted)
            delivered=call.call_args.args[0]
            self.assertTrue(all(delivered[k]==v for k,v in s.items()))
            self.assertEqual(r['raw_forecast']['probability_yes'],1.)
            self.assertEqual(r['payload']['probability_yes'],.98);self.assertEqual(s,before)

    def test_paired_replay_with_http_blocked_no_attempts_or_budget_renewal(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}):
            parent=Path(tmp)/'input.json';save(parent,table_bundle());root=Path(tmp)/'trial'
            def super_call(messages,key,**kwargs):
                common=json.loads(messages[1]['content']);record={'request':{'model':brief.SUPER},'status':'reserved'}
                token=kwargs['observer']('reserve',record)
                output={'tool_calls':[{'function':{'name':'record_forecast_brief','arguments':json.dumps(proposed(common))}}]}
                record.update(status='received',response={'usage':{'prompt_tokens':10,'completion_tokens':10}})
                kwargs['observer']('complete',record,token);return output
            def mercury_call(state,heads,key,observer,**kwargs):
                r={'status':'reserved','request':{'model':'mercury'}};t=observer('reserve',r)
                ans=response(heads);r.update(status='received',response=ans);observer('complete',r,t);return ans
            with patch.object(brief,'ask_model',side_effect=super_call),patch.object(brief.decision_http,'decide',side_effect=mercury_call):
                first=brief_trial.run([parent],root,execute=True,max_http=3)
            self.assertEqual(first['completed'],1);self.assertEqual(first['new_http'],3)
            with patch.object(brief,'ask_model',side_effect=AssertionError('Unwanted Super HTTP')),patch.object(
                    brief.decision_http,'decide',side_effect=AssertionError('Unwanted Mercury HTTP')):
                second=brief_trial.run([parent],root,execute=True,max_http=3)
            self.assertEqual(first,second)
            with self.assertRaises(ValueError):brief_trial.run([parent],root,execute=True,max_http=4)

    def test_brief_failure_scores_originals_without_format_repair(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}):
            parent=Path(tmp)/'input.json';save(parent,table_bundle())
            def mercury(state,heads,key,observer,**kwargs):
                r={'request':{'model':'mercury'},'status':'reserved'};t=observer('reserve',r)
                ans=response(heads);r.update(status='received',response=ans);observer('complete',r,t);return ans
            with patch.object(brief,'brief_stage',side_effect=ValueError('Malformed brief')),patch.object(
                    brief.decision_http,'decide',side_effect=mercury):
                r=brief_trial.run([parent],Path(tmp)/'trial',execute=True,max_http=3)
            self.assertEqual(r['completed'],1);self.assertEqual(r['cases'][0]['brief_status'],'unavailable')
            self.assertFalse(r['cases'][0]['arms']['brief']['brief_delivered'])
            self.assertEqual(r['new_http'],2)
            self.assertEqual(r['paired_completed'],0)


if __name__=='__main__':unittest.main()
