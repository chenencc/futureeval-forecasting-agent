"""Bounded zero-body repairs, short issuers and honest execution states."""
import copy
import hashlib
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.acquisition import pipeline, recovery
from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.runtime.collection_actions import primary_rescue
from ForecastAgent.runtime.source_frontier import rescue_before_close, recover_before_stall
from ForecastAgent.competition.queue import save, load, digest
from ForecastAgent.releases import v1_0_4 as release

REQUEST = {'id':'1','question':'What is the IMF global growth projection?',
           'resolution_criteria':'Use IMF published data','question_type':'numeric',
           'mode':'live','pipeline':'collection','acquisition_profile':'collection_v3',
           'recover_sources_before_stall':True}


def closed_bundle():
    return {'request':REQUEST,'pages':{},'searches':[], 'exa_searches':[],
            'source_leads':{'https://imf.org/report':{'url':'https://imf.org/report','origin':'saved_search','title':'IMF growth projection'}},
            'fetch_attempts':[{'url':'https://imf.org/report','status':'failed','detail':'HTTP Error 403: Forbidden'}],
            'model_attempts':[{'status':'received'}],
            'result':{'status':'leads_only','incomplete':True,'resumable':True,
                      'termination_reason':'program_dispatch_limit',
                      'execution_report':{'owner':'program','interrupted':False}}}


class RecoveryTests(TestCase):
    def test_development_cli_cannot_submit(self):
        with patch('sys.argv',['release','--root','unused','--snapshots','unused','--submit']), \
             patch.object(release,'verify_release',return_value={'development_only':True}), \
             patch.object(release,'supervise',side_effect=AssertionError('Worker started')):
            with self.assertRaisesRegex(ValueError,'Development collection repair'):
                release.main()

    def test_transport_failure_reports_unknown_usage_without_inventing_http_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);record={'request':{'model':'offline'},'status':'transport_error','error':'URLError'}
            save(root/'model_calls/0001.json',record)
            raw=(root/'model_calls/0001.json').read_bytes()
            bundle={'model_attempts':[{'path':'model_calls/0001.json','sha256':hashlib.sha256(raw).hexdigest()}],
                    'fetch_attempts':[{'status':'failed','detail':'URL is not a public HTTP URL'}]}
            report=pipeline.resource_report(bundle,root,root/'supplement')
            self.assertEqual(report['model_transport_error_types'],{'URLError':1})
            self.assertEqual(report['model_failures_without_http_status'],1)
            self.assertEqual(report['unknown_usage_attempts'],1)
            self.assertIn('preflight',report['initial_source_attempts_scope'])

    def test_release_empty_export_does_not_proceed_to_analysis_or_rewrite_native_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);native=root/'release-1.0.4';raw=closed_bundle()
            raw['result']['incomplete']=False
            save(native/'collection/bundle.json',raw);save(native/'package.json',raw)
            save(native/'state.json',{'stage':'complete'})
            save(native/'identity.json',{'offline':True})
            before=(native/'collection/bundle.json').read_bytes()
            with patch.object(release,'verify_release'),patch.object(release.pipeline,'identity',return_value={'offline':True}), \
                 patch.object(release.pipeline,'run',return_value={'state':'complete'}):
                adapter=release.collect(REQUEST,root)
            self.assertTrue(adapter['result']['incomplete'])
            self.assertEqual(adapter['result']['termination_reason'],'supplement_without_material')
            self.assertEqual((native/'collection/bundle.json').read_bytes(),before)
            self.assertEqual(adapter['original_collector_result'],raw['result'])

    def test_zero_body_closed_leads_are_supplement_ready_not_verified(self):
        bundle=closed_bundle()
        self.assertTrue(recovery.supplement_ready(bundle))
        self.assertTrue(release.raw_handoff_eligible(bundle))
        self.assertFalse(recovery.has_readable_material(bundle))
        self.assertEqual(recovery.execution_state(bundle)['halt_kind'],'bounded_closure')
        self.assertFalse(recovery.execution_state(bundle)['interrupted'])

    def test_failed_unknown_or_interrupted_receipts_never_advance(self):
        for status in ('reserved','transport_error','http_error'):
            bundle=closed_bundle();bundle['model_attempts'][0]['status']=status
            self.assertFalse(recovery.supplement_ready(bundle))
            self.assertFalse(release.raw_handoff_eligible(bundle))
        bundle=closed_bundle();bundle['result']['termination_reason']='model_transport_failure'
        bundle['result']['execution_report']['interrupted']=True
        self.assertFalse(recovery.supplement_ready(bundle))
        self.assertTrue(recovery.execution_state(bundle)['interrupted'])

    def task(self, root, issuer='IMF', host='imf.org'):
        task=RetrievalTask(root,REQUEST)
        task.bundle['plan']=[{'id':'n','priority':'critical','expected_source':issuer,'condition':'Growth projection'}]
        url='https://'+host+'/report'
        task.bundle['searches']=[{'status':'completed','results':[{'url':url,'title':'Projection'}]}]
        task.bundle['fetch_attempts']=[{'url':url,'status':'failed','detail':'HTTP Error 403: Forbidden'}]
        task.save()
        return task,url

    def test_short_issuers_match_failed_hosts_and_generic_words_do_not(self):
        with tempfile.TemporaryDirectory() as directory:
            for index,(issuer,host) in enumerate([('IMF outlook','imf.org'),('WHO report','who.int'),('AAA price','gasprices.aaa.com')]):
                task,url=self.task(Path(directory)/str(index),issuer,host)
                self.assertEqual(primary_rescue(task)[0]['url'],url)
            task,_=self.task(Path(directory)/'generic','Official source data','api.news.org')
            self.assertEqual(primary_rescue(task),[])

    def test_critical_rescue_uses_one_existing_extract_without_new_search_or_model(self):
        with tempfile.TemporaryDirectory() as directory:
            task,url=self.task(Path(directory))
            original_searches=copy.deepcopy(task.bundle['searches'])
            exa_remaining=task.budget()['exa_search_remaining']
            with patch('ForecastAgent.runtime.retrieval.extract_basic',return_value={
                    'results':[{'url':url,'raw_content':'IMF published the global growth outlook. '*40}],
                    'failed_results':[]}) as extract:
                rescue_before_close(task,'offline-key')
                self.assertEqual(task.budget()['basic_extract_batches_remaining'],0)
                self.assertIsNone(rescue_before_close(task,'offline-key'))
                restored=RetrievalTask(Path(directory),REQUEST)
                self.assertIsNone(rescue_before_close(restored,'offline-key'))
                self.assertEqual(extract.call_count,1)
            self.assertEqual(task.bundle['searches'],original_searches)
            self.assertEqual(task.bundle.get('model_attempts',[]),[])
            self.assertEqual(task.budget()['tavily_basic_remaining'],2)
            self.assertEqual(task.budget()['exa_search_remaining'],exa_remaining)

    def test_explicit_extract_decline_and_missing_key_are_honored(self):
        with tempfile.TemporaryDirectory() as directory:
            task,_=self.task(Path(directory))
            self.assertIsNone(rescue_before_close(task,''))
            task.bundle['channel_decisions']={'tavily_extract_basic':{'decision':'deferred'}}
            with patch('ForecastAgent.runtime.retrieval.extract_basic',side_effect=AssertionError('Provider called')):
                self.assertIsNone(rescue_before_close(task,'offline-key'))

    def test_free_recovery_with_all_failed_reads_is_completed_with_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            task,url=self.task(Path(directory))
            task.bundle['fetch_attempts']=[]
            with patch.object(task,'execute',return_value={'reads':[{'url':url,'ok':False}]}), \
                 patch('ForecastAgent.runtime.source_frontier.unread_candidates',return_value=[{'url':url}]):
                recover_before_stall(task,'offline-key')
            self.assertEqual(task.bundle['control']['source_recovery_events'][0]['status'],'completed_with_gaps')

    def test_pipeline_routes_closed_zero_body_snapshot_without_repeating_collector(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ',{'FORECAST_MODEL':'offline'}):
            root=Path(directory);identity=pipeline.identity(REQUEST,False)
            raw=closed_bundle();raw['request']=identity['request']
            native=RetrievalTask(root/'collection',identity['request']);native.bundle.update(raw);raw=native.bundle
            save(root/'identity.json',identity)
            save(root/'state.json',{'stage':'collection','identity_sha256':pipeline.digest(identity)})
            save(root/'collection/bundle.json',raw)
            before=(root/'collection/bundle.json').read_bytes()
            captured=copy.deepcopy(raw)
            captured['pages']={'https://imf.org/report':{'url':'https://imf.org/report',
                'content':'Growth projection evidence. '*40,'body_diagnostics':{'usable_text':True}}}
            with patch('ForecastAgent.agent.run_research',side_effect=AssertionError('Collector repeated')), \
                 patch('ForecastAgent.supplement.stage.run',return_value=[]), \
                 patch('ForecastAgent.supplement.stage.analysis_overlay',return_value=captured), \
                 patch('ForecastAgent.runtime.intelligent_acquisition.terminal_report',return_value={}), \
                 patch('ForecastAgent.evidence.acceptance.collection_acceptance',return_value={'status':'accepted_with_gaps'}):
                report=pipeline.run(REQUEST,root,supplement_network=False)
            self.assertEqual(report['state'],'complete')
            self.assertTrue(report['readable_material_available'])
            self.assertEqual((root/'collection/bundle.json').read_bytes(),before)
            self.assertEqual(load(root/'state.json')['collector_execution']['halt_kind'],'bounded_closure')

    def test_pipeline_does_not_call_export_factual_completion_when_still_no_bodies(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ',{'FORECAST_MODEL':'offline'}):
            root=Path(directory);identity=pipeline.identity(REQUEST,False)
            raw=closed_bundle();raw['request']=identity['request']
            native=RetrievalTask(root/'collection',identity['request']);native.bundle.update(raw);raw=native.bundle
            save(root/'identity.json',identity);save(root/'state.json',{'stage':'collection','identity_sha256':pipeline.digest(identity)})
            save(root/'collection/bundle.json',raw)
            with patch('ForecastAgent.agent.run_research',side_effect=AssertionError('Collector repeated')), \
                 patch('ForecastAgent.supplement.stage.run',return_value=[]), \
                 patch('ForecastAgent.supplement.stage.analysis_overlay',return_value=raw), \
                 patch('ForecastAgent.runtime.intelligent_acquisition.terminal_report',return_value={}), \
                 patch('ForecastAgent.evidence.acceptance.collection_acceptance',return_value={'status':'failed'}):
                report=pipeline.run(REQUEST,root,supplement_network=False)
            self.assertFalse(report['readable_material_available'])
            self.assertEqual(report['capture_integrity']['status'],'failed')
