"""Offline gates for shared budgets, immutable originals and conservative roles."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ForecastAgent.acquisition import eager_capture as capture
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.acquisition import source_profile


def page(html, text):
    raw = html.encode()
    return {'url':'https://example.org/report', 'final_url':'https://example.org/report',
        'content_type':'text/html', 'charset':'utf-8', 'content':text,
        'sha256':hashlib.sha256(raw).hexdigest(), 'raw_response_base64':base64.b64encode(raw).decode(),
        'retrieved_at_utc':'2026-10-14T00:00:00Z', 'documents':[]}


def request():
    return {'id':'123','question':'Will the Alpha agency publish a 2026 report?',
        'resolution_criteria':'An official Alpha report published in 2026.', 'mode':'live',
        capture.FIELD:capture.identity()}


class CaptureTests(unittest.TestCase):
    def test_actual_search_handler_captures_before_return_and_duplicate_read_is_free(self):
        from ForecastAgent.runtime import retrieval
        with tempfile.TemporaryDirectory() as root:
            task=RetrievalTask(Path(root),request())
            task.bundle['plan']=[{'id':'N1','priority':'critical','condition':'Alpha report',
                'expected_source':'Alpha agency','query':'Alpha report 2026'}]
            urls=['https://alpha.gov/2026/report','https://news.example.org/alpha-report-2026']
            hits=[{'url':u,'title':'Alpha agency 2026 official report','content':'Saved search lead'} for u in urls]
            text='The Alpha agency published its official report on October 14, 2026. '*8
            def fetched(url):
                value=page('<main><p>'+text+'</p></main>',text);value.update(url=url,final_url=url);return value
            with patch.object(retrieval,'search_batch',return_value={'results':hits}),patch.object(retrieval,'fetch_public_page',side_effect=fetched) as fetch,capture.adapter():
                reply=task.execute('search_tavily',{'query':'Alpha report 2026','need_ids':['N1'],'reason':'Find official report'},'fixture')
                self.assertEqual(reply['eager_capture']['budget']['page_fetch_remaining'],6)
                self.assertEqual(len(task.bundle['pages']),2)
                self.assertEqual(fetch.call_count,2)
                task.execute('fetch_page',{'url':urls[0]},None)
                self.assertEqual(fetch.call_count,2)
            self.assertEqual(len(task.bundle['searches']),1)
            self.assertEqual(len(task.bundle['fetch_attempts']),2)
            self.assertEqual(len(task.bundle['eager_capture_events']),1)
            restored=RetrievalTask(Path(root),task.bundle['request'])
            self.assertEqual(len(restored.bundle['fetch_attempts']),2)
            self.assertEqual(restored.budget()['page_fetch_remaining'],6)
    def test_empty_shell_is_excluded_but_real_short_notice_and_rows_are_retained(self):
        shell=page('<nav><a href="/home">Home</a><a href="/contact">Contact</a></nav>','Home Contact')
        self.assertFalse(capture.diagnostic(shell,shell['url'])['usable_text'])
        notice=page('<main><p>The Alpha agency published its official report on October 14, 2026.</p></main>',
                    'The Alpha agency published its official report on October 14, 2026.')
        self.assertTrue(capture.diagnostic(notice,notice['url'])['usable_text'])
        rows=page('<header>2026-10-14 2.6%</header>','2026-10-14 2.6%')
        rows['documents']=[{'page_content':'2026-10-14 2.6%','metadata':{'format':'table'}}]
        rows['rows']=[['2026-10-14','2.6%']]
        self.assertTrue(capture.diagnostic(rows,rows['url'])['usable_text'])
        header_notice=page('<header>Agency operations were suspended on October 14, 2026 due to a technical issue.</header>',
            'Agency operations were suspended on October 14, 2026 due to a technical issue.')
        self.assertTrue(capture.diagnostic(header_notice,header_notice['url'])['usable_text'])

    def test_preview_is_kept_as_a_lead_not_counted_as_full_body(self):
        preview=page('<article><h1>Alpha report</h1></article>','Alpha report publication update')
        d=capture.diagnostic(preview,preview['url'])
        self.assertTrue(d['usable_text']); self.assertFalse(d['qualifies_full_body'])

    def test_article_about_regional_restriction_is_not_a_shell(self):
        text='The Alpha agency report describes why the service was not available in your country during the event. '+ 'Reported findings remain public. '*20
        value=page('<article><p>'+text+'</p></article>',text)
        self.assertTrue(capture.diagnostic(value,value['url'])['usable_text'])

    def test_final_export_preserves_all_original_bytes_and_is_idempotent(self):
        source={'request':request(),'pages':{'https://example.org/report':page('<main><p>Alpha report.</p></main>','Alpha report publication update')},'fetch_attempts':[]}
        before=copy.deepcopy(source)
        with tempfile.TemporaryDirectory() as root:
            result=capture.export(source,root)
            self.assertEqual(source,before)
            self.assertEqual(result['pages']['https://example.org/report']['content'],source['pages']['https://example.org/report']['content'])
            Path(root,'capture-export-package.json').write_text(json.dumps(result))
            self.assertEqual(capture.export(source,root),result)
            self.assertEqual(len(list(Path(root,'eager-final/packets').glob('*.json'))),1)

    def test_source_object_tampering_is_detected(self):
        source={'request':request(),'pages':{'https://example.org/report':page('<p>Alpha report</p>','Alpha report publication update')}}
        with tempfile.TemporaryDirectory() as root:
            capture.snapshot(source,root,'first')
            first=capture.snapshot(source,root,'first')
            obj=Path(root,'objects',first['sources']['https://example.org/report']['object_sha256']+'.json');obj.write_text('{}')
            with self.assertRaises(ValueError):capture.snapshot(source,root,'second')

    def test_snapshot_replays_searches_and_request_without_later_bundle(self):
        source={'request':request(),'pages':{},'searches':[{'query':'Alpha','results':[]}], 'plan':[{'id':'N1'}]}
        with tempfile.TemporaryDirectory() as root:
            packet=capture.snapshot(source,root,'first')
            for name in ('request','plan','searches','exa_searches'):
                value=json.loads(Path(root,'objects',packet['context_objects'][name]+'.json').read_text())
                self.assertEqual(value,source.get(name,[]))

    def test_existing_final_export_tampering_is_detected(self):
        source={'request':request(),'pages':{}}
        with tempfile.TemporaryDirectory() as root:
            result=capture.export(source,root);result['pages']['invented']={'content':'changed'}
            Path(root,'capture-export-package.json').write_text(json.dumps(result))
            with self.assertRaises(ValueError):capture.export(source,root)

    def test_unflagged_tasks_keep_native_search_without_eager_reads(self):
        from ForecastAgent.runtime import retrieval
        with tempfile.TemporaryDirectory() as root:
            req=request();req.pop(capture.FIELD);task=RetrievalTask(Path(root),req)
            task.bundle['plan']=[{'id':'N1'}]
            with patch.object(retrieval,'search_batch',return_value={'results':[]}),patch.object(capture,'eager') as eager,capture.adapter():
                reply=task.execute('search_tavily',{'query':'Alpha','need_ids':['N1'],'reason':'Find Alpha'},'fixture')
            self.assertNotIn('eager_capture',reply);eager.assert_not_called()

    def test_failed_search_keeps_original_error_and_search_reservation(self):
        from ForecastAgent.runtime import retrieval
        with tempfile.TemporaryDirectory() as root:
            task=RetrievalTask(Path(root),request());task.bundle['plan']=[{'id':'N1'}]
            with patch.object(retrieval,'search_batch',side_effect=RuntimeError('offline outage')),capture.adapter():
                with self.assertRaisesRegex(RuntimeError,'Tavily attempt failed'):
                    task.execute('search_tavily',{'query':'Alpha','need_ids':['N1'],'reason':'Find Alpha'},'fixture')
            self.assertEqual(task.bundle['searches'][0]['status'],'failed')
            self.assertEqual(len(task.bundle['searches']),1)
            self.assertEqual(task.budget()['page_fetch_remaining'],8)

    def test_release_collection_export_and_supplement_use_frozen_derivative(self):
        from ForecastAgent.releases import v1_0_5 as release
        from ForecastAgent.competition.queue import save
        req=request();req.pop(capture.FIELD)
        original={'request':req,'pages':{'https://example.org/report':page('<main><p>Alpha report publication update</p></main>','Alpha report publication update')},'result':{'status':'collected'}}
        original['pages']['https://example.org/report']['body_diagnostics']={'usable_text':True}
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root);directory=folder/'retrieval/release-1.0.5'
            save(directory/'collection/bundle.json',original);save(directory/'package.json',original)
            save(directory/'state.json',{'stage':'complete'});save(directory/'identity.json',{'fixture':True})
            before=(directory/'collection/bundle.json').read_bytes()
            with patch.object(release,'verify_release'),patch.object(release.pipeline,'identity',return_value={'fixture':True}),patch.object(release.pipeline,'run',return_value={'state':'complete'}):
                marker=release.collect(req,folder/'retrieval')
            self.assertEqual(marker['release_acquisition']['package_file'],'capture-export-package.json')
            exported=release.supplement(marker,folder,req['id'])
            self.assertEqual(exported['pages']['https://example.org/report']['content'],original['pages']['https://example.org/report']['content'])
            self.assertEqual((directory/'collection/bundle.json').read_bytes(),before)

    def test_two_automatic_reads_leave_existing_slots_for_agent(self):
        with tempfile.TemporaryDirectory() as root:
            task=RetrievalTask(Path(root),request());task.bundle['plan']=[{'id':'N1','priority':'critical'}]
            rows=[{'url':f'https://example{i}.org/report','canonical_url':f'https://example{i}.org/report',
                'priority':5,'origin':'search','need_ids':['N1']} for i in range(10)]
            def perform(name,args,key):
                self.assertEqual(name,'fetch_page');task.bundle['fetch_attempts'].append({'url':args['url'],'status':'failed'})
                raise RuntimeError('Preserved failure')
            with patch.object(capture,'candidates',side_effect=lambda t:[r for r in rows if r['url'] not in {x['url'] for x in t.bundle['fetch_attempts']}]),patch.object(task,'execute',side_effect=perform):
                for _ in range(4):capture.eager(task)
            self.assertEqual(len(task.bundle['fetch_attempts']),6)
            self.assertEqual(task.budget()['page_fetch_remaining'],2)
            self.assertEqual(len(task.bundle['searches']),0)
            self.assertEqual(len(task.bundle.get('model_attempts',[])),0)

    def test_no_reads_in_historical_or_finished_tasks(self):
        with tempfile.TemporaryDirectory() as root:
            task=RetrievalTask(Path(root),request());task.bundle['plan']=[{'id':'N1'}]
            task.cutoff='2026-10-01'
            with patch.object(task,'execute') as call:capture.eager(task);call.assert_not_called()
            task.cutoff=None;task.bundle['result']={'status':'collected'}
            with patch.object(task,'execute') as call:capture.eager(task);call.assert_not_called()

    def test_exact_observed_document_keeps_parent_hash_and_no_recursive_read(self):
        value=page('<main><a href="/alpha.pdf">Alpha 2026 report</a></main>','Alpha 2026 report')
        rows=source_profile.observed_details(value,value['url'])
        self.assertEqual(rows[0]['url'],'https://example.org/alpha.pdf')
        self.assertEqual(rows[0]['parent_source_sha256'],value['sha256']);self.assertEqual(rows[0]['depth'],1)

    def test_policy_drift_is_not_a_new_quota(self):
        with tempfile.TemporaryDirectory() as root:
            task=RetrievalTask(Path(root),request());task.bundle['request'][capture.FIELD]['per_search']=3
            with self.assertRaises(ValueError):capture.enabled(task)


if __name__=='__main__':unittest.main()
