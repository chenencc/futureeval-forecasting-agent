"""Observed dependency recovery without real providers or forecasting labels."""
import base64
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.analysis.pilot import load
from ForecastAgent.evidence.source_identity import page_links
from ForecastAgent.supplement import enhanced, frontier, materials


def dataset_bundle():
    url='https://example.org/station/87654321/01.09.2026'
    page={'url':url,'content':'Download raw data files for the last 31 days. Older historical data can be downloaded from the archive.',
        'links':[url+'/down.txt','https://example.org/archive/historical-data']}
    return {'request':{'id':1,'question':'What is the river level on September 1, 2026?',
        'resolution_criteria':'Use station 87654321 in cm at 08:00 CEST from '+url,
        'experiment_id':'offline-material-test'},'pages':{url:page},'searches':[], 'exa_searches':[], 'fetch_attempts':[]}


class MaterialRecoveryTests(unittest.TestCase):
    def test_empty_supplement_journal_is_durable_and_restartable(self):
        b={'request':{'question':'A subject with no captured sources'},'pages':{},
           'searches':[],'exa_searches':[],'fetch_attempts':[]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(enhanced,'fetch_document') as http:
            enhanced.run(b,tmp,network=True)
            self.assertEqual(load(Path(tmp)/'state.json'),{'attempts':[]})
            enhanced.run(b,tmp,network=True)
            self.assertEqual(load(Path(tmp)/'state.json'),{'attempts':[]})
            self.assertFalse(http.called)

    def test_preferred_venue_does_not_promote_unrelated_result_children(self):
        q={'question':'When will an LLM achieve gold on the International Math Olympiad?',
           'resolution_criteria':'Use https://example.org/olympiad'}
        page={'content':'International Math Olympiad gold results.','links':[]}
        route=materials.dependency(q,'https://example.org/olympiad',page,
            {'url':'https://example.org/participant/results','label':'Participant results'})
        self.assertEqual(route['role'],'ordinary_detail')

    def test_string_relative_markdown_and_verified_labels(self):
        html=b'<a href="report.csv?date=2026-09-01&amp;station=11">Download measurements</a>'
        p={'links':['report.csv?date=2026-09-01&station=11',{'href':'notes.pdf','text':'Notes'}],
            'content':'[Historical archive](https://example.org/archive)',
            'raw_response_base64':base64.b64encode(html).decode(),'sha256':hashlib.sha256(html).hexdigest()}
        links=page_links(p,'https://example.org/daily/')
        self.assertEqual(len(links),3)
        self.assertEqual(links[0]['label'],'Download measurements')
        self.assertEqual(links[0]['url'],'https://example.org/daily/report.csv?date=2026-09-01&station=11')
        p['sha256']='wrong'
        self.assertEqual(page_links(p,'https://example.org/daily/')[0]['label'],'')

    def test_adapter_empty_links_can_recover_verified_main_html(self):
        html=b'<nav><a href="login">Login</a></nav><a href="Q2-2026.pdf">Earnings presentation</a>'
        p={'links':[],'content':'Saved earnings release','raw_response_base64':base64.b64encode(html).decode(),
            'sha256':hashlib.sha256(html).hexdigest()}
        links=page_links(p,'https://example.org/earnings')
        self.assertEqual(links,[{'url':'https://example.org/Q2-2026.pdf','label':'Earnings presentation','observed_in':'verified_saved_html'}])

    def test_news_feed_does_not_lend_topic_to_unrelated_results(self):
        q={'question':'What is the outcome of Jane Smith murder trial?'}
        page={'content':('Jane Smith murder trial. Other news and navigation. '*2000),'links':[]}
        route=materials.dependency(q,'https://example.org/trial',page,{'url':'https://example.org/elections/results','label':'Election results'})
        self.assertEqual(route['role'],'ordinary_detail')
        small={'content':'Jane Smith murder trial update.','links':[]}
        route=materials.dependency(q,'https://example.org/trial',small,{'url':'https://example.org/elections/results','label':'Election results'})
        self.assertEqual(route['role'],'ordinary_detail')

    def test_directory_routes_target_file_and_archive_not_other_day(self):
        b=dataset_bundle();parent=next(iter(b['pages']))
        b['pages'][parent]['links'].append('https://example.org/station/87654321/02.09.2026/down.txt')
        plan=enhanced.plan(b);r=frontier.admit(plan['sources'],b['request'],pages=b['pages'])
        self.assertIn(parent+'/down.txt',[x['url'] for x in r['accepted']])
        self.assertIn('https://example.org/archive/historical-data',[x['url'] for x in r['accepted']])
        self.assertTrue(any(x['deferred_reason']=='different_target_day_file' for x in r['deferred']))
        self.assertIn('target_data_file_missing',[x['reason'] for x in plan['gaps']])
        self.assertFalse(plan['material_coverage']['target_data_candidate_saved'])

    def test_existing_readable_directory_reaches_measurements_without_refetch(self):
        b=dataset_bundle();parent=next(iter(b['pages']));before=copy.deepcopy(b)
        body='01.09.2026\n87654321\ncm\n07:45#17\n08:00#18\n08:15#19'
        def fetch(url):
            self.assertNotEqual(url,parent)
            return {'url':url,'content':body if url.endswith('down.txt') else 'Historical raw measurement download instructions. '*5,'links':[]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(enhanced,'fetch_document',side_effect=fetch) as http, patch.object(enhanced,'render_page') as browser:
            out=enhanced.run(b,tmp,network=True,max_link_depth=2)
            self.assertIn(parent+'/down.txt',out['pages'])
            self.assertTrue(enhanced.plan(out)['material_coverage']['target_data_candidate_saved'])
            count=http.call_count
            enhanced.run(b,tmp,network=True,max_link_depth=2)
            self.assertEqual(http.call_count,count);self.assertEqual(browser.call_count,0)
            self.assertEqual(len(load(Path(tmp)/'state.json')['attempts']),count)
        self.assertEqual(b,before)

    def test_primary_attachment_inherits_context_without_truth(self):
        q={'question':'How much digital advertising will Example Publishing Company report in Q2 2026?',
           'resolution_criteria':'Use https://example.org/earnings'}
        b={'request':q,'pages':{'https://example.org/earnings':{'content':'Example Publishing Company Q2 2026 digital advertising revenues report. '*4,
             'links':[{'href':'https://assets.example.org/Q2-2026-Presentation.pdf','text':'Presentation'},
                      {'href':'https://assets.example.org/Q1-2026-Presentation.pdf','text':'Earlier presentation'}]}},'searches':[],'exa_searches':[]}
        b['pages']['https://example.org/earnings']['links'].append({'href':'https://assets.example.org/Q2-2025-Presentation.pdf','text':'Last year'})
        r=frontier.admit(enhanced.plan(b)['sources'],q,pages=b['pages'])
        link=next(x for x in r['accepted'] if x['url'].endswith('Q2-2026-Presentation.pdf'))
        self.assertEqual(link['material_dependency']['role'],'source_attachment')
        self.assertFalse(link['material_dependency']['source_identity_verified'])
        self.assertFalse(any('Q1-2026' in x['url'] for x in r['accepted']))
        self.assertFalse(any('Q2-2025' in x['url'] for x in r['accepted']))

    def test_offline_and_exhausted_caps_never_fetch_or_reset(self):
        b=dataset_bundle();b['fetch_attempts']=[{'status':'reserved'}]*16
        with tempfile.TemporaryDirectory() as tmp, patch.object(enhanced,'fetch_document') as http, patch.object(enhanced,'render_page') as browser:
            enhanced.run(b,tmp,network=False,max_link_depth=2)
            self.assertFalse(http.called);self.assertFalse(browser.called)
            self.assertEqual(load(Path(tmp)/'state.json')['attempts'] if (Path(tmp)/'state.json').exists() else [],[])
        with tempfile.TemporaryDirectory() as tmp, patch.object(enhanced,'fetch_document') as http, patch.object(enhanced,'render_page') as browser:
            prior=[{'attempts':[{'tool':'browser','status':'reserved'}]*6}]
            enhanced.run(b,tmp,network=True,prior=prior,max_link_depth=2)
            self.assertFalse(http.called);self.assertFalse(browser.called)
            self.assertEqual(load(Path(tmp)/'report.json')['usage']['http'],16)


if __name__=='__main__':unittest.main()
