"""Post-supplement integration preserves capture provenance and shared caps."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.analysis.pilot import digest, save
from ForecastAgent.research_loop import post_supplement as post, runtime, state, gap_feedback
from ForecastAgent.runtime import retrieval
from ForecastAgent.tests.test_research_gap_feedback import task, initial
from ForecastAgent.tests.test_research_simple_map import literal_proposal
from ForecastAgent.acquisition import pipeline


class PostSupplementTests(unittest.TestCase):
    def fake_model(self, messages, api_key, **kwargs):
        data = json.loads(messages[1]['content'])
        packet = data['reading']
        span = next(s for s in packet['evidence'] if 'Revenue was 24 billion dollars.' in s['text'])
        p = literal_proposal(self.original)
        p['expected_revision'] = data['expected_revision']
        p['material_sha256'] = packet['material_sha256']
        p['nodes'] = p['nodes'][:1]
        p['nodes'][0].update(evidence_ids=[span['evidence_id']], event_stage='unknown')
        p['material_requests'] = []
        p['update_mode'] = 'merge'
        p['material_reviews'] = [{'material_id':span['material_id'], 'disposition':'incorporated',
            'evidence_ids':[span['evidence_id']], 'node_ids':['observed_report'], 'gap_ids':[],
            'related_material_ids':[], 'effect':'narrows', 'reason':'Dated background; not the target result.'}]
        observer = kwargs['observer']
        record = {'status':'reserved', 'request':{'model':'test-model'}}
        token = observer('reserve', record)
        record.update(status='received', response={'usage':{'total_tokens':100}}, http_status=200)
        observer('complete', record, token)
        return {'role':'assistant', 'tool_calls':[{'id':'map', 'type':'function',
            'function':{'name':'update_research_state', 'arguments':json.dumps(p)}}]}

    def test_saved_bodies_reviewed_once_with_no_capture_or_budget_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = task(Path(tmp)/'original')
            self.original = copy.deepcopy(t.bundle)
            frozen = digest(self.original)
            with patch.dict('os.environ', {'OPENROUTER_API_KEY':'test'}), patch(
                    'ForecastAgent.research_loop.post_supplement.ask_model', side_effect=self.fake_model) as model:
                child, report = post.run(self.original, Path(tmp)/'review')
                again, cached = post.run(self.original, Path(tmp)/'review')
            self.assertEqual(model.call_count,1)
            self.assertEqual(report['status'],'reviewed')
            self.assertEqual(report,cached)
            self.assertEqual(child,again)
            self.assertEqual(digest(self.original),frozen)
            for key in post.PRESERVED:
                self.assertEqual(child.get(key),self.original.get(key))
            self.assertEqual(report['usage']['totals']['http_attempts'],1)
            self.assertEqual(report['usage']['totals']['known_total_tokens'],100)
            self.assertEqual(child['research_loop']['revision'],1)
            self.assertFalse(child['research_acquisition']['pending_map_update'])

    def test_unknown_reserved_http_is_not_retried_after_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = task(Path(tmp)/'original')
            root = Path(tmp)/'review'
            post.run(t.bundle, root, execute=False)
            save(root/'model-http/001.json', {'status':'reserved','request':{'model':'test'}})
            with patch('ForecastAgent.research_loop.post_supplement.ask_model') as model:
                child, report = post.run(t.bundle,root)
            model.assert_not_called()
            self.assertEqual(report['status'],'interrupted_receipt_requires_review')
            self.assertEqual(child['research_loop']['revision'],0)
            self.assertEqual(report['usage']['totals']['unknown_total_tokens_attempts'],1)

    def test_budget_reservation_is_restored_and_remaining_counts_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp); request=t.bundle['request'];request[post.FIELD]=post.POLICY
            old=(retrieval.COLLECTION_MAX_TURNS,retrieval.COLLECTION_HTTP_PER_DISPATCH,retrieval.MAX_RUN_SECONDS)
            with self.assertRaises(RuntimeError):
                with post.reserve_collection(request):
                    self.assertEqual(retrieval.COLLECTION_MAX_TURNS,max(0,old[0]-2))
                    self.assertEqual(retrieval.COLLECTION_HTTP_PER_DISPATCH,max(0,old[1]-2))
                    raise RuntimeError('test')
            self.assertEqual(old,(retrieval.COLLECTION_MAX_TURNS,retrieval.COLLECTION_HTTP_PER_DISPATCH,retrieval.MAX_RUN_SECONDS))
            b={'model_attempts':[{'status':'received'}]*10+[{'status':'transport_error'}]*4}
            limits={'post_http_cap':2,'lifetime_http_cap':16,'lifetime_decision_cap':12,'lifetime_failure_cap':6}
            self.assertEqual(post.remaining_http(b,limits),2)
            self.assertEqual(post.remaining_http(b,{**limits,'lifetime_failure_cap':4}),0)
            b['model_attempts'].extend([{'status':'received'}]*2)
            self.assertEqual(post.remaining_http(b,limits),0)

    def test_no_allowance_keeps_map_and_gap_instead_of_calling(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(Path(tmp)/'original')
            with patch('ForecastAgent.research_loop.post_supplement.ask_model') as model:
                child, report=post.run(t.bundle,Path(tmp)/'review',http_cap=0)
            model.assert_not_called()
            self.assertEqual(report['status'],'no_reserved_model_allowance')
            self.assertEqual(child['research_loop'],t.bundle['research_loop'])
            self.assertTrue(child['research_acquisition']['pending_map_update'])

    def test_initial_graph_schema_and_final_revision_reserve(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp)
            tools=runtime.filter_tools(t,runtime.configure(t,[]))
            update=next(x for x in tools if x['function']['name']=='update_research_state')
            self.assertEqual(update['function']['parameters']['properties']['nodes']['minItems'],1)
            initial(t)
            t.bundle['request'][post.FIELD]=post.POLICY
            t.bundle['request_hash']=hashlib.sha256(json.dumps(t.bundle['request'],sort_keys=True).encode()).hexdigest()
            # Filter uses the remaining revision allowance, without making an HTTP call.
            with patch('ForecastAgent.research_loop.runtime.view',return_value={
                    'remaining_updates':1,'revision':2,'material_sha256':'a'*64}):
                names={x['function']['name'] for x in runtime.filter_tools(t,runtime.configure(t,[]))}
            self.assertNotIn('update_research_state',names)

    def test_packet_reads_new_sources_fairly_and_keeps_exact_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(tmp)
            for i in range(20):
                t.bundle['pages']['https://example.org/extra'+str(i)]={
                    'content':('Other currency and period baseline; not target evidence.\n'*20)}
            before=copy.deepcopy(t.bundle['pages'])
            packet=post.reading_packet(t)
            self.assertLessEqual(len(packet['evidence']),16)
            self.assertLessEqual(sum(len(s['text']) for s in packet['evidence']),24000)
            self.assertTrue(packet['undelivered_materials'])
            self.assertEqual(t.bundle['pages'],before)
            catalog=state.catalog(t.bundle)
            for s in packet['evidence']:
                self.assertEqual(s['text'],catalog['spans'][s['evidence_id']]['text'])
                self.assertEqual(s['body_sha256'],catalog['sources'][s['url']]['body_sha256'])

    def test_complete_pipeline_includes_reserved_review_and_caches_without_recollection(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=task(Path(tmp)/'fixture')
            t.bundle['request'][post.FIELD]=post.POLICY
            t.bundle['request_hash']=hashlib.sha256(json.dumps(t.bundle['request'],sort_keys=True).encode()).hexdigest()
            t.bundle['result']={'acquisition_complete':True,'incomplete':False}
            self.original=copy.deepcopy(t.bundle)
            request=copy.deepcopy(t.bundle['request'])
            frozen={'request':request,'input_warnings':[],'test_identity':True}
            seen=[]
            def collect(req,root):
                seen.append(retrieval.COLLECTION_MAX_TURNS)
                save(root/'bundle.json',self.original)
                return copy.deepcopy(self.original)
            with patch.dict('os.environ',{'OPENROUTER_API_KEY':'test'}), patch(
                    'ForecastAgent.agent.run_research',side_effect=collect) as collector, patch(
                    'ForecastAgent.acquisition.pipeline.identity',return_value=frozen), patch(
                    'ForecastAgent.supplement.stage.run') as supplement, patch(
                    'ForecastAgent.supplement.stage.analysis_overlay',side_effect=lambda b,*a:copy.deepcopy(b)), patch(
                    'ForecastAgent.acquisition.pipeline.terminal_report',return_value={}), patch(
                    'ForecastAgent.evidence.acceptance.collection_acceptance',return_value={}), patch(
                    'ForecastAgent.research_loop.post_supplement.ask_model',side_effect=self.fake_model) as model:
                old=retrieval.COLLECTION_MAX_TURNS
                root=Path(tmp)/'pipeline'
                report=pipeline.run(request,root)
                second=pipeline.run(request,root)
            self.assertEqual(seen,[max(0,old-2)])
            self.assertEqual(collector.call_count,1)
            self.assertEqual(supplement.call_count,1)
            self.assertEqual(model.call_count,1)
            self.assertEqual(report,second)
            self.assertEqual(report['post_supplement_map']['status'],'reviewed')
            self.assertEqual(report['resources']['model_http_attempts_including_post_map'],1)
            self.assertEqual(json.loads((root/'collection/bundle.json').read_text()),self.original)

    def test_intelligence_review_runs_after_data_recovery_under_same_reservation(self):
        from ForecastAgent.intelligence import pipeline as intelligence
        import time
        with tempfile.TemporaryDirectory() as tmp:
            t=task(Path(tmp)/'fixture')
            t.bundle['request'][post.FIELD]=post.POLICY
            captured=copy.deepcopy(t.bundle)
            repaired=copy.deepcopy(captured)
            repaired['pages']['https://example.org/recovered']={'content':'A separate saved data repair.'}
            root=Path(tmp)/'intelligence'
            reservation={'limits':post.budget(), 'started_at_epoch':time.time()}
            save(root/'retrieval/release-1.0.5/map-reservation.json',reservation)
            save(root/'retrieval/release-1.0.5/state.json',{'post_reservation_sha256':digest(reservation)})
            order=[]
            def recover(*args,**kwargs):order.append('recover');return repaired,{'completed':True}
            def review(b,*args,**kwargs):
                order.append('review')
                self.assertIn('https://example.org/recovered',b['pages'])
                self.assertLessEqual(kwargs['http_cap'],2)
                return b,{'status':'partial_review'}
            with patch('ForecastAgent.releases.v1_0_5.collect',return_value={'release_acquisition':{}}) as collector, patch(
                    'ForecastAgent.releases.v1_0_5.supplement',return_value=captured), patch(
                    'ForecastAgent.intelligence.repair.recover',side_effect=recover), patch(
                    'ForecastAgent.research_loop.post_supplement.run',side_effect=review), patch(
                    'ForecastAgent.intelligence.pipeline.prepare_package',return_value=({},{})):
                collector.return_value={'release_acquisition':{'version':'1.0.5'}}
                intelligence._collect(t.bundle['request'],root,clock_utc='2026-10-10T00:00:00Z')
            self.assertEqual(order,['recover','review'])
            self.assertEqual(collector.call_args.args[0][post.STAGE_FIELD],'intelligence_after_data_recovery')


if __name__=='__main__':
    unittest.main()
