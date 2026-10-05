"""The experimental replay cannot fetch and scores only exact frozen material."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition.frontier_compare import inputs, seeded, run_arm, metrics, run_case


def call(name, args, ident):
    return {'id':ident,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}


class FrontierComparisonTests(TestCase):
    def test_v2_is_explicit_separate_identity_and_cannot_migrate_saved_v1_task(self):
        data,_=inputs()
        with TemporaryDirectory() as tmp:
            old=seeded(data['cases'][0],'candidate',Path(tmp)/'old')
            new=seeded(data['cases'][0],'candidate',Path(tmp)/'new',repair_v2=True)
            self.assertEqual(old.bundle['request']['acquisition_strategy'],'intelligent_materials_v1')
            self.assertEqual(new.bundle['request']['acquisition_strategy'],'intelligent_materials_v2')
            self.assertEqual(old.bundle['pages'],new.bundle['pages'])
            with self.assertRaisesRegex(ValueError,'different input'):
                seeded(data['cases'][0],'candidate',Path(tmp)/'old',repair_v2=True)
            with self.assertRaisesRegex(ValueError,'fixed Super'):
                run_case(data['cases'][0]['id'],Path(tmp)/'invalid',repair_v2=True)

    def test_v2_harness_forces_exact_review_and_rejects_cache_policy_migration(self):
        from ForecastAgent.tests.test_intelligent_repairs import saved_cases, adapt_saved_plan
        data,_=inputs()
        case=data['cases'][0]
        saved=next(c for c in saved_cases() if c['id']==case['id'])
        old_plan=next(r['arguments'] for r in saved['calls'] if r['tool']=='plan_evidence')
        read=next(r['arguments'] for r in saved['calls'] if r['tool']=='read_document')
        with TemporaryDirectory() as tmp, patch.dict('os.environ',{'EXA_API_KEY':''}):
            root=Path(tmp)
            task=seeded(case,'candidate',root,repair_v2=True)
            plan=adapt_saved_plan(task,old_plan)
            turns=[]
            def respond(projected,*args,**kwargs):
                turns.append(kwargs.get('forced_tool'))
                if len(turns)==1:
                    return {'tool_calls':[call('plan_evidence',plan,'p')]}
                if len(turns)==2:
                    return {'tool_calls':[call('read_document',read,'r')]}
                self.assertEqual(kwargs['forced_tool'],'review_passages')
                focus=next(json.loads(m['content']) for m in projected if m['role']=='user'
                    and json.loads(m['content']).get('kind')=='pending_material_review')
                return {'tool_calls':[call('review_passages',{'items':[{'passage_id':r['passage_id'],
                    'action':'keep','need_ids':[plan['needs'][0]['id']],
                    'reason':'Keep complete dated table rows with units and labels.'} for r in focus['passages']]},'k'),
                    call('finish_collection',{'gaps':['Independent series and assessments remain missing.']},'f')]}
            with patch('ForecastAgent.runtime.retrieval.ask_ultra',side_effect=respond) as model, \
                 patch('ForecastAgent.providers.http.download') as fetch:
                report=run_arm(case,'candidate',root,'mock-key',http_cap=11,repair_v2=True)
            self.assertEqual(model.call_count,3)
            fetch.assert_not_called()
            self.assertEqual(report['issues'],[])
            self.assertEqual(report['material_checks_selected'],2)
            with patch('ForecastAgent.runtime.retrieval.ask_ultra') as cached:
                self.assertEqual(run_arm(case,'candidate',root,'mock-key',http_cap=11,repair_v2=True),report)
                with self.assertRaisesRegex(ValueError,'policy changed'):
                    run_arm(case,'candidate',root,'mock-key')
                cached.assert_not_called()

    def test_pool_and_preregistered_anchors_are_exact(self):
        data, rubric = inputs()
        self.assertEqual(sum(len(c['pages']) for c in data['cases']),33)
        self.assertEqual(sum(len(x) for x in rubric['targets'].values()),15)
        self.assertFalse(rubric['promotion_allowed'])

    def test_arms_have_identical_material_and_no_imported_attempts(self):
        data,_ = inputs()
        with TemporaryDirectory() as tmp:
            one = seeded(data['cases'][0],'baseline',Path(tmp)/'one')
            two = seeded(data['cases'][0],'candidate',Path(tmp)/'two')
            self.assertEqual(one.bundle['pages'],two.bundle['pages'])
            self.assertEqual(one.catalog(),two.catalog())
            self.assertEqual(one.budget()['tavily_basic_remaining'],0)
            self.assertEqual(two.budget()['exa_search_remaining'],0)
            self.assertEqual(one.bundle['searches'],[])
            self.assertEqual(two.bundle['excerpts'],[])

    def test_runtime_replay_banks_exact_row_blocks_network_and_caches_completion(self):
        data,rubric = inputs()
        case = data['cases'][0]
        target = rubric['targets'][case['id']][0]['alternatives'][0]
        quote = 'USDT\nTether USDt\nUSDT\n$184,368,461,962.97'
        plan = {'needs':[{'id':'rows','condition':'USDT and USDC market cap at the target date',
            'priority':'critical','expected_source':'CoinMarketCap target historical snapshot',
            'query':'USDT USDC June 30 market cap','question_spans':[{'field':'question','quote':'June 30, 2026'}]}],
            'entity_card':{'subject':'USDT and USDC','identity_checks':'Both exact stablecoin symbols',
            'required_form':'Dated capitalization table','announcement_window':'By June 30, 2026',
            'effective_vs_announcement':'Observation date differs from capture date'}}
        messages = [{'tool_calls':[call('plan_evidence',plan,'p')]},
            {'tool_calls':[call('read_sources',{'urls':[target['url']],
                'queries':[{'query':'USDT market cap','need_ids':['rows']}]},'r')]},
            {'tool_calls':[call('record_quote',{'url':target['url'],'quote':quote,
                'occurrence_index':1,'need_ids':['rows']},'q'),
                call('finish_collection',{'gaps':['Full daily combined series unavailable.']},'f')]}]
        with TemporaryDirectory() as tmp, patch.dict('os.environ',{'EXA_API_KEY':''}):
            root=Path(tmp)
            with patch('ForecastAgent.runtime.retrieval.ask_ultra',side_effect=messages) as model, \
                 patch('ForecastAgent.providers.http.download') as physical_fetch:
                report=run_arm(case,'candidate',root,'mock-key')
            self.assertEqual(model.call_count,3)
            physical_fetch.assert_not_called()
            self.assertEqual(report['issues'],[])
            self.assertEqual(report['resources']['initial_source_http_attempts'],0)
            self.assertEqual(report['material_checks_selected'],1)
            with patch('ForecastAgent.runtime.retrieval.ask_ultra') as cached_model:
                self.assertEqual(run_arm(case,'candidate',root,'mock-key'),report)
                cached_model.assert_not_called()
            bundle=json.loads((root/'bundle.json').read_text(encoding='utf-8'))
            forged=copy.deepcopy(bundle)
            forged['excerpts'][0]['text']+=' invented data'
            audit=metrics(forged,root,case,rubric['targets'][case['id']])
            self.assertTrue(audit['issues'])
            self.assertEqual(audit['material_checks_selected'],0)
