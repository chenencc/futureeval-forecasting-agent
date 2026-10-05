"""The experimental replay cannot fetch and scores only exact frozen material."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition.frontier_compare import inputs, seeded, run_arm, metrics


def call(name, args, ident):
    return {'id':ident,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}


class FrontierComparisonTests(TestCase):
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
