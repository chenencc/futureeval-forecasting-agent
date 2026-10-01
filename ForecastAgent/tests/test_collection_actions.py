"""Material selection and repetition guards must not spend provider allowances."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.runtime.retrieval import RetrievalTask
from ForecastAgent.runtime.collection_actions import next_action, pending_passages, duplicate_read
from ForecastAgent.runtime.progress import delivered
from ForecastAgent.runtime.contracts import validate, ContractError
from ForecastAgent.tools.registry import COLLECTION_TOOLS
from ForecastAgent.runtime.tool_selection import active_tools
from ForecastAgent.debug_trial import run_one

URL='https://example.org/update'
TEXT='Atlas is available on macOS. Windows release development is ongoing and the published update describes supported platforms.'
REQUEST={'question':'Will Atlas release for Windows?', 'resolution_criteria':'Public availability',
    'background':'Official source: https://openai.com/index/introducing-chatgpt-atlas/',
    'pipeline':'collection','acquisition_profile':'collection_v3','collection_temporal_policy':'current_information'}


def setup_task(root):
    task=RetrievalTask(root,REQUEST)
    task.bundle['plan']=[{'id':'n','condition':'Release availability','priority':'critical','expected_source':'Official OpenAI page','query':'Windows release'}]
    task.bundle['pages'][URL]={'url':URL,'content':TEXT,'sha256':'raw','temporal_status':'live_capture'}
    result=task.execute('read_sources',{'urls':[],'queries':[{'query':'Windows release','need_ids':['n']}]},'')
    task.bundle['transcript'].append({'tool':'read_sources','result':{'data':result}})
    return task


class CollectionActionsTests(TestCase):
    def test_primary_status_banner_located_even_when_query_only_mentions_windows(self):
        with TemporaryDirectory() as temp:
            task=RetrievalTask(Path(temp),REQUEST)
            task.bundle['plan']=[{'id':'n','priority':'critical','expected_source':'OpenAI blog','query':'Windows availability'}]
            url='https://openai.com/index/introducing-chatgpt-atlas'
            body='This post introduced ChatGPT Atlas. Atlas has since been deprecated.\n\n'+TEXT
            task.bundle['pages'][url]={'url':url,'content':body,'sha256':'official','temporal_status':'live_capture'}
            self.assertEqual(next_action(task)['urls'],[url])
            result=task.execute('read_sources',{'urls':[url],'queries':[{'query':'Windows availability','need_ids':['n']}]},'')
            self.assertIn('deprecated',result['reads'][0]['source_header_preview'])
            passages=[p for row in result['located_material'] for p in row['passages']]
            self.assertTrue(any('deprecated' in p['text'] for p in passages))
            task.bundle['transcript'].append({'tool':'read_sources','result':{'data':result}})
            self.assertEqual(next_action(task)['tool'],'review_passages')
            self.assertEqual(task.bundle['control']['status_scanned_versions'][url],
                __import__('ForecastAgent.readers.saved',fromlist=['version_digest']).version_digest(task.bundle['pages'][url]))

    def test_named_product_release_notes_routed_within_existing_fetch_budget(self):
        with TemporaryDirectory() as temp:
            task=RetrievalTask(Path(temp),REQUEST)
            task.bundle['plan']=[{'id':'n','priority':'critical','expected_source':'OpenAI website'}]
            url='https://help.openai.com/en/articles/chatgpt-atlas-release-notes'
            task.bundle['source_leads'][url]={'url':url,'origin':'page_link'}
            self.assertEqual(next_action(task)['urls'],[url])
            task.bundle['fetch_attempts']=[{'url':url,'status':'failed'}]
            self.assertEqual(next_action(task)['tool'],'extract_failed_pages')
            task.bundle['extract_attempts']=[{'status':'completed','urls':[url]}]
            self.assertFalse(next_action(task))

    def test_candidate_kept_exactly_then_no_more_pending(self):
        with TemporaryDirectory() as temp:
            task=setup_task(Path(temp))
            before=task.budget()
            action=next_action(task)
            self.assertEqual(action['tool'],'review_passages')
            candidate=action['candidates'][0]
            result=task.execute('review_passages',{'items':[{'passage_id':candidate['passage_id'],'need_ids':['n'],
                'action':'keep','reason':'Platform availability is relevant'}]},'')
            self.assertTrue(result['items'][0]['ok'])
            self.assertEqual(task.bundle['excerpts'][0]['text'],TEXT)
            self.assertFalse(pending_passages(task))
            self.assertEqual(before,task.budget())

    def test_rejection_is_recorded_without_manufactured_excerpt(self):
        with TemporaryDirectory() as temp:
            task=setup_task(Path(temp))
            pid=pending_passages(task)[0]['passage_id']
            args={'items':[{'passage_id':pid,'need_ids':[],
                'action':'reject','reason':'This illustrative candidate is not needed'}]}
            validate(task,'review_passages',args,COLLECTION_TOOLS)
            result=task.execute('review_passages',args,'')
            self.assertTrue(result['items'][0]['ok'])
            self.assertFalse(task.bundle['excerpts'])
            self.assertFalse(pending_passages(task))
            self.assertFalse(task.bundle['passage_dispositions'][pid]['truth_verified'])
            with self.assertRaises(ContractError):
                validate(task,'review_passages',{'items':[{**args['items'][0],'action':'keep'}]},COLLECTION_TOOLS)

    def test_duplicate_delivered_read_blocked_but_new_version_allowed(self):
        with TemporaryDirectory() as temp:
            task=setup_task(Path(temp))
            args={'url':URL,'document_index':1}
            result=task.execute('read_document',args,'')
            delivered(task,'read_document',args,result)
            self.assertTrue(duplicate_read(task,{'url':URL}))
            before=task.budget()
            self.assertNotIn('read_document',[t['function']['name'] for t in active_tools(task,COLLECTION_TOOLS)])
            with self.assertRaises(ContractError) as error:
                validate(task,'read_document',args,COLLECTION_TOOLS)
            self.assertEqual(error.exception.details['code'],'already_delivered_range')
            self.assertEqual(before,task.budget())
            task.bundle['pages'][URL]['content']+=' New release material.'
            self.assertFalse(duplicate_read(task,args))

    @patch('ForecastAgent.runtime.retrieval.extract_basic')
    def test_primary_rescue_once_then_read_new_body(self,extract):
        with TemporaryDirectory() as temp:
            task=RetrievalTask(Path(temp),REQUEST)
            task.bundle['plan']=[{'id':'n','priority':'critical','expected_source':'Official OpenAI announcement'}]
            url='https://openai.com/index/introducing-chatgpt-atlas'
            task.bundle['fetch_attempts']=[{'url':url,'status':'failed'}]
            task.bundle['pages'][URL]={'url':URL,'content':TEXT,'sha256':'secondary','temporal_status':'live_capture'}
            task.bundle['excerpts']=[{'url':URL,'text':TEXT,'need_ids':['n']}]
            self.assertEqual(next_action(task)['tool'],'extract_failed_pages')
            extract.return_value={'results':[{'url':url,'raw_content':TEXT}]}
            task.execute('extract_failed_pages',{'urls':[url],'need_ids':['n'],'reason':'Rescue named primary page'},'test-only')
            self.assertEqual(next_action(task)['tool'],'read_sources')
            self.assertEqual(next_action(task)['urls'],[url])
            self.assertEqual(task.budget()['basic_extract_batches_remaining'],0)
            self.assertEqual(extract.call_count,1)
            with self.assertRaises(ValueError):
                task.execute('extract_failed_pages',{'urls':[url],'need_ids':['n'],'reason':'Repeat'},'test-only')

    def test_explicit_reopen_once_without_quota_change(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'batch.json').write_text(json.dumps({'tasks':['1','2']}))
            for qid in ('1','2'):
                task=RetrievalTask(root/'tasks'/qid,{**REQUEST,'id':qid})
                task.bundle['result']={'incomplete':False,'summary':'Old result'}
                task.save()
            other=(root/'tasks'/'2'/'bundle.json').read_bytes()
            def resume(request,directory,*keys):
                task=RetrievalTask(directory,request)
                task.bundle['result']={'incomplete':False,'summary':'Repaired'}
                task.save()
                return task.bundle
            with patch('ForecastAgent.debug_trial.run_retrieval',side_effect=resume):
                result=run_one(root,'1','test','test',resume_reason='Material selection repair')
                self.assertTrue(all(result['ledger_checks'].values()))
                with self.assertRaises(ValueError):
                    run_one(root,'1','test','test',resume_reason='Material selection repair')
            self.assertEqual(other,(root/'tasks'/'2'/'bundle.json').read_bytes())
