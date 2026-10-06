"""Production failure regressions with no real provider or submission calls."""
import copy
import json
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.providers.financial import source_urls
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.runtime.contracts import validate, ContractError
from ForecastAgent.runtime.retrieval import RetrievalTask, run_retrieval
from ForecastAgent.runtime.source_frontier import unread_candidates, recover_before_stall
from ForecastAgent.runtime.tool_selection import active_tools
from ForecastAgent.supplement.stage import run, analysis_overlay
from ForecastAgent.tools.registry import COLLECTION_TOOLS
from ForecastAgent.tests.test_collection import page, call
from ForecastAgent.tests.test_runtime_contracts import prepared, LIVE

RULE = 'https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=CONS&sectionNum=SEC.%203.&article=IV'
ARTICLE = 'https://www.gov.ca.gov/2026/09/18/governor-newsom-ai-order/'


def archived(folder, bundle):
    path = folder/'parent.zip'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('campaign.json', json.dumps({'tasks':{'1':{'status':'closed_with_gaps'}}}))
        z.writestr('tasks/1/bundle.json', json.dumps(bundle))
    return path


class ProductionCollectionTests(TestCase):
    def test_url_query_keys_survive_markdown_and_html_decoding(self):
        source = RULE.replace('&', r'\&')
        self.assertEqual(source_urls('[Constitution]('+source+')'), [RULE])
        self.assertEqual(source_urls(RULE.replace('&', '&amp;')), [RULE])
        self.assertEqual(source_urls('https://example.org/report?q=A%26B&notable=1'),
                         ['https://example.org/report?q=A%26B&notable=1'])

    @patch('ForecastAgent.runtime.retrieval.save_cached_page')
    @patch('ForecastAgent.runtime.retrieval.cached_page', return_value=None)
    @patch('ForecastAgent.runtime.retrieval.fetch_public_page')
    def test_mixed_batch_reads_known_variants_without_spending_on_guesses(self, fetch, cached, save):
        fetch.return_value = page()
        with tempfile.TemporaryDirectory() as root:
            task = prepared(root)
            task.bundle['source_leads'].update({u:{'url':u,'origin':'question_resolution_criteria'} for u in [RULE, ARTICLE]})
            from ForecastAgent.tavily_research import canonical_url
            task.bundle['source_leads']={canonical_url(u):r for u,r in task.bundle['source_leads'].items()}
            args = {'urls':['https://www.gov.ca.gov/', 'https://www.gov.ca.gov/category/proclamations/',
                            ARTICLE.rstrip('/'), RULE.replace('%20','+')],
                    'queries':[{'query':'Release', 'need_ids':['n']}], 'rescue_failed':False}
            validate(task,'read_sources',args,active_tools(task,COLLECTION_TOOLS))
            result=task.execute('read_sources',args,'')
            self.assertEqual([r['ok'] for r in result['reads']], [False,False,True,True])
            self.assertEqual(fetch.call_count,2)
            self.assertEqual({c.args[0] for c in fetch.call_args_list}, {RULE,ARTICLE})
            self.assertEqual(task.budget()['page_fetch_remaining'],6)
            self.assertEqual(result['reads'][0]['contract_error']['code'],'unknown_source')
            validate(task,'fetch_page',{'url':ARTICLE.rstrip('/')},active_tools(task,COLLECTION_TOOLS))
            with self.assertRaises(ContractError):
                validate(task,'fetch_page',{'url':'http://127.0.0.1/'},active_tools(task,COLLECTION_TOOLS))

    def test_shell_checks_preserve_short_real_announcements_and_tables(self):
        fixture=json.loads((Path(__file__).parent/'fixtures'/'production_46056_shells.json').read_text(encoding='utf-8'))
        for row in fixture:
            self.assertFalse(body_diagnostics(row['content'])['usable_text'],row['url'])
        for body in ('Governor issued the proclamation today.',
                     '| Date | Value |\n| 2026-10-05 | 42 |',
                     'Menu\nPrivacy policy\nThe agency published the June statistics today, including the employment rate, regional breakdown, and revision history for all published figures.'):
            self.assertTrue(body_diagnostics(body)['usable_text'])

    def test_supplement_captures_unread_rule_sources_and_retains_shell_gaps(self):
        fixture=json.loads((Path(__file__).parent/'fixtures'/'production_46056_shells.json').read_text(encoding='utf-8'))
        old={'request':{'question':'Will the agency publish?', 'resolution_criteria':RULE+' '+ARTICLE},
             'mode':'live','pages':{fixture[0]['url']:{**fixture[0], 'body_diagnostics':{'state':'readable','usable_text':True}}},
             'fetch_attempts':[], 'searches':[], 'exa_searches':[], 'failed_captures':[]}
        before=copy.deepcopy(old)
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);archive=archived(folder,old);raw=archive.read_bytes()
            with patch('ForecastAgent.supplement.stage.fetch_document',return_value=page()) as http, \
                 patch('ForecastAgent.supplement.stage.render_page',side_effect=RuntimeError('Unavailable')) as browser:
                result=run(archive,folder/'out',['1'],network=True)
                run(archive,folder/'out',['1'],network=True)
                self.assertEqual(http.call_count,2)
                self.assertEqual(browser.call_count,1)
                self.assertEqual(result[0]['new_readable_captures'],2)
            overlay=analysis_overlay(old,folder/'out','1')
            self.assertFalse(overlay['pages'][fixture[0]['url']]['body_diagnostics']['usable_text'])
            self.assertTrue(overlay['gaps'][0]['capture_gap_count'])
            self.assertEqual(old,before)
            self.assertEqual(archive.read_bytes(),raw)
            child=json.loads((folder/'out/tasks/1/supplement.json').read_text())
            self.assertEqual(child['provider_calls'],{'models':0,'tavily':0,'exa':0})

    def test_unread_sources_do_not_bypass_historical_or_reserved_attempt_guards(self):
        with tempfile.TemporaryDirectory() as d:
            old={'request':{'question':'Agency', 'resolution_criteria':RULE+' '+ARTICLE},
                 'mode':'historical_strict','pages':{},'fetch_attempts':[{'url':ARTICLE,'status':'reserved'}]}
            self.assertEqual([r['url'] for r in unread_candidates(old)],[RULE])
            archive=archived(Path(d),old)
            with patch('ForecastAgent.supplement.stage.fetch_document') as http,patch('ForecastAgent.supplement.stage.render_page') as browser:
                run(archive,Path(d)/'out',['1'],network=True)
                http.assert_not_called();browser.assert_not_called()

    def test_recovery_is_durable_and_never_resets_search_allowances(self):
        with tempfile.TemporaryDirectory() as root:
            task=prepared(root,{**LIVE,'recover_sources_before_stall':True,'resolution_criteria':RULE})
            before=task.budget()
            with patch.object(task,'execute',return_value={'reads':[]}) as execute:
                self.assertIsNotNone(recover_before_stall(task,''))
                self.assertIsNone(recover_before_stall(task,''))
                execute.assert_called_once()
            task.save();restored=RetrievalTask(Path(root),task.bundle['request'])
            self.assertIsNone(recover_before_stall(restored,''))
            self.assertEqual(before,restored.budget())

    def test_equal_priority_official_sources_use_normalized_dates(self):
        b={'request':{'question':'Will Governor Newsom act on AI?'},'searches':[{'results':[
            {'url':'https://www.gov.ca.gov/2025/12/16/newsom-ai/', 'title':'Newsom AI', 'published_date':'Tue, 16 Dec 2025 16:37:13 GMT'},
            {'url':'https://www.gov.ca.gov/2026/09/18/newsom-ai/', 'title':'Newsom AI', 'published_date':'2026-09-18T00:00:00Z'}]}]}
        self.assertIn('/2026/',unread_candidates(b)[0]['url'])

    @patch('ForecastAgent.runtime.retrieval.save_cached_page')
    @patch('ForecastAgent.runtime.retrieval.cached_page',return_value=None)
    @patch('ForecastAgent.runtime.retrieval.fetch_public_page',return_value=page())
    @patch('ForecastAgent.runtime.retrieval.ask_ultra')
    def test_soft_stall_delivers_one_capture_batch_before_finalization(self, model, fetch, cache, save):
        model.side_effect=[call('list_sources',{},str(i)) for i in range(3)]+[call('finish_collection',{'gaps':['Remaining sources unread']},'end')]
        request={**LIVE,'recover_sources_before_stall':True,'resolution_criteria':RULE}
        with tempfile.TemporaryDirectory() as root:
            task=prepared(root,request);task.save()
            result=run_retrieval(request,root,'','')
            self.assertEqual(model.call_count,4)
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(result['control']['source_recovery_events'][0]['status'],'completed')
            self.assertEqual(len(result['searches']),0)
