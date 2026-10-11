"""Source-local paging uses real native readers and a shared physical journal."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ForecastAgent.research_loop import post_supplement as post, source_review


class SourceNavigationTests(unittest.TestCase):
    def fixture(self, root):
        from ForecastAgent.tests.test_research_source_review import SourceReviewTests
        helper = SourceReviewTests()
        task = helper.fixture(root)
        task.bundle['pages']['https://example.org/a']['content'] = (
            ('Earlier background from a different event and period. '*26+'\n\n')*7 +
            ('Target needle metric is 42 units; its applicability is unverified. '*28+'\n\n')*2)
        return task, helper

    def emit(self, kwargs, name, args):
        observer = kwargs['observer']
        record = {'status':'reserved','request':{'model':'fixture'}}
        token = observer('reserve',record)
        record.update(status='received',response={'usage':{'total_tokens':10}})
        observer('complete',record,token)
        return {'tool_calls':[{'function':{'name':name,'arguments':json.dumps(args)}}]}

    def run_trial(self, task, folder, cap, reply):
        with patch.dict('os.environ',{'OPENROUTER_API_KEY':'fixture'}), \
             patch.object(post,'ask_model',side_effect=reply), \
             patch('urllib.request.urlopen',side_effect=AssertionError('No external network')):
            return post.run(task.bundle,folder,http_cap=cap)

    def test_pages_then_queries_before_one_update_and_retains_prior_originals(self):
        with tempfile.TemporaryDirectory() as root:
            t, helper = self.fixture(root)
            pages = copy.deepcopy(t.bundle['pages']); budget=t.budget(); packets=[]; updates=[]
            final = helper.model(updates,['irrelevant','irrelevant'])
            def reply(messages,key,**kwargs):
                packet=json.loads(messages[1]['content']);packets.append(packet)
                names={entry['function']['name'] for entry in kwargs['tools']}
                index=len(packets)
                if index==1:
                    self.assertIn('inspect_research_state',names)
                    cursor=packet['reading']['reading_cursor']['next_span_offset']
                    self.assertIsNotNone(cursor)
                    return self.emit(kwargs,'inspect_research_state',
                        {'url':packet['scheduled_source']['url'],'offset':cursor,'limit':4})
                if index==2:
                    return self.emit(kwargs,'inspect_research_state',
                        {'url':packet['scheduled_source']['url'],'query':'Target needle','offset':0,'limit':2})
                self.assertNotIn('inspect_research_state',names)
                return final(messages,key,**kwargs)
            child,report=self.run_trial(t,Path(root)/'post',4,reply)
            self.assertEqual(len(packets),4);self.assertEqual(len(updates),2)
            first=report['steps'][0]
            self.assertEqual(first['actual_http_attempts'],3);self.assertEqual(len(first['navigation']),2)
            self.assertTrue(first['accepted']);self.assertTrue(report['steps'][1]['accepted'])
            self.assertEqual(packets[2]['prior_local_readings'][0]['reading']['evidence'],packets[0]['reading']['evidence'])
            self.assertEqual(packets[2]['prior_local_readings'][1]['reading']['evidence'],packets[1]['reading']['evidence'])
            self.assertTrue(any('Target needle' in r['text'] for r in packets[2]['reading']['evidence']))
            self.assertEqual(report['usage']['totals']['http_attempts'],4)
            self.assertEqual(child['pages'],pages);self.assertEqual(t.budget(),budget)
            for field in post.PRESERVED:self.assertEqual(child.get(field),t.bundle.get(field))

    def test_repeated_read_stops_without_receipt_and_does_not_starve_next_source(self):
        with tempfile.TemporaryDirectory() as root:
            t,helper=self.fixture(root);packets=[];updates=[]
            final=helper.model(updates,['irrelevant'])
            def reply(messages,key,**kwargs):
                packet=json.loads(messages[1]['content']);packets.append(packet)
                if len(packets)==1:
                    return self.emit(kwargs,'inspect_research_state',
                        {'url':packet['scheduled_source']['url'],'offset':0,'limit':3})
                return final(messages,key,**kwargs)
            _,report=self.run_trial(t,Path(root)/'post',4,reply)
            self.assertEqual(len(packets),2)
            self.assertEqual(report['steps'][0]['status'],'local_reading_no_progress')
            self.assertFalse(report['steps'][0]['accepted'])
            self.assertTrue(report['steps'][0]['material_still_pending'])
            self.assertTrue(report['steps'][1]['accepted'])
            self.assertEqual(report['material_processing']['pending_material_count'],1)

    def test_other_url_is_rejected_before_inspection_and_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            t,helper=self.fixture(root);updates=[];calls=[]
            final=helper.model(updates,['irrelevant'])
            def reply(messages,key,**kwargs):
                calls.append(json.loads(messages[1]['content']))
                if len(calls)==1:
                    return self.emit(kwargs,'inspect_research_state',
                        {'url':'https://example.org/report','limit':3})
                return final(messages,key,**kwargs)
            _,report=self.run_trial(t,Path(root)/'post',4,reply)
            self.assertEqual(report['steps'][0]['status'],'proposal_rejected')
            self.assertEqual(report['steps'][0]['actual_http_attempts'],1)
            self.assertNotIn('navigation',report['steps'][0]['outcome'] if 'outcome' in report['steps'][0] else {})
            self.assertTrue(report['steps'][1]['accepted'])

    def test_physical_retries_cannot_take_later_source_reservation(self):
        with tempfile.TemporaryDirectory() as root:
            t,helper=self.fixture(root);calls=[];updates=[]
            final=helper.model(updates,['irrelevant'])
            def reply(messages,key,**kwargs):
                calls.append(json.loads(messages[1]['content']))
                if len(calls)==1:
                    for _ in range(3):
                        # With a total cap of three, the first source has only
                        # two slots. The third reserve fails before transport.
                        self.emit(kwargs,'inspect_research_state',{'url':'https://example.org/a','limit':3})
                    raise AssertionError('Unreachable third physical reservation')
                return final(messages,key,**kwargs)
            _,report=self.run_trial(t,Path(root)/'post',3,reply)
            self.assertEqual(len(calls),2)
            self.assertEqual(report['steps'][0]['status'],'source_http_cap_exhausted')
            self.assertEqual(report['steps'][0]['actual_http_attempts'],2)
            self.assertTrue(report['steps'][1]['accepted'])
            self.assertEqual(report['usage']['totals']['http_attempts'],3)

    def test_prior_reading_preserves_unknown_metadata_and_literal_scope(self):
        from ForecastAgent.research_loop import dispatch, gap_feedback, state
        with tempfile.TemporaryDirectory() as root:
            t,_=self.fixture(root); source=gap_feedback.pending(t)[0]
            reading=t.execute('inspect_research_state',{'url':source['url'],'limit':3},'')
            reading['future_extension']={'unit':'literal unknown metadata'}
            reading['source_catalog']={'large_navigation':'x'*50000}
            before=copy.deepcopy(reading)
            choice={'material_id':source['material_id'],'url':source['url'],
                    'revision':t.bundle['research_loop']['revision'],
                    'material_sha256':state.catalog(t.bundle)['material_sha256']}
            packet=json.loads(source_review.messages(t,reading,choice,prior_readings=[reading])[1]['content'])
            saved=packet['prior_local_readings'][0]
            self.assertEqual(saved['reading']['future_extension'],before['future_extension'])
            self.assertEqual(saved['reading']['evidence'],before['evidence'])
            self.assertEqual(saved['reading']['source_context'],before['source_context'])
            self.assertIn('source_catalog',{v['field'] for v in saved['omitted_global_metadata']})
            self.assertEqual(reading,before)


if __name__=='__main__':unittest.main()
