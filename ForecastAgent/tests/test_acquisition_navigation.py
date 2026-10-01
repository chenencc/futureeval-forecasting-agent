"""Regression cases for delivery, date navigation and durable exit inventories."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ForecastAgent.tests.test_runtime_contracts import prepared, URL
from ForecastAgent.tests.test_delivery import group, reply
from ForecastAgent.runtime.context import collection_context, encode
from ForecastAgent.runtime.delivery import acknowledge
from ForecastAgent.runtime.needs import inventory
from ForecastAgent.runtime.contracts import ContractError
from ForecastAgent.readers.saved import find_passages


class AcquisitionNavigationTests(TestCase):
    def test_filtered_month_pages_and_confirmed_delivery(self):
        with TemporaryDirectory() as root:
            task = prepared(root)
            page = task.bundle['pages'][URL]
            page['rows'] = [{'date':'2025-09-08','value':1},
                {'date':'2026-08-03','value':5.23},{'date':'2026-08-04','value':5.18},
                {'date':'2026-09-01','value':5.3}]
            page['documents'] = [{'page_content':json.dumps(r),'metadata':{}} for r in page['rows']]
            args = {'url':URL,'start_date':'2026-08-01','end_date':'2026-08-31','limit':1,'need_ids':['n']}
            result = task.execute('read_dataset_rows',args,'')
            self.assertEqual(result['total'],2)
            self.assertEqual(result['rows'][0]['date'],'2026-08-03')
            self.assertEqual(result['row_locations'][0]['document_index'],2)
            group(task,args,result,'read_dataset_rows')
            view = collection_context(task)
            acknowledge(task,view)
            self.assertEqual(inventory(task.bundle)['needs'][0]['material_state'],'rows_delivered')
            receipt = next(iter(task.bundle['progress']['delivery_receipts'].values()))
            self.assertEqual(receipt['visible_range']['date_filter']['start_date'],'2026-08-01')
            second = task.execute('read_dataset_rows',{**args,'offset':reply(view)['next_offset']},'')
            self.assertEqual(second['rows'][0]['date'],'2026-08-04')
            self.assertIsNone(second['next_offset'])
            self.assertEqual(task.budget()['tavily_basic_remaining'],3)

    def test_date_filter_rejects_partial_and_reversed_dates(self):
        with TemporaryDirectory() as root:
            task = prepared(root); task.bundle['pages'][URL]['rows'] = []
            for args in ({'start_date':'2026-08-01'},
                         {'start_date':'2026-09-01','end_date':'2026-08-01'}):
                with self.assertRaises(ValueError):
                    task.execute('read_dataset_rows',{'url':URL,**args},'')

    def test_outside_month_is_empty_without_claiming_full_dataset_empty(self):
        with TemporaryDirectory() as root:
            task=prepared(root)
            task.bundle['pages'][URL]['rows']=[{'date':'2026-08-03','value':5}]
            result=task.execute('read_dataset_rows',{'url':URL,'start_date':'2026-01-01','end_date':'2026-01-31'},'')
            self.assertEqual(result['total'],0)
            self.assertEqual(result['source_row_count'],1)
            self.assertEqual(result['observed_end'],'2026-08-03')

    def test_checkpoint_cannot_displace_latest_exact_source(self):
        with TemporaryDirectory() as root:
            task=prepared(root)
            args={'url':URL,'max_chars':100}
            result=task.execute('read_document',args,'')
            group(task,args,result)
            task.bundle['messages'].append({'role':'user','content':json.dumps({'acquisition_checkpoint':'x'*100000})})
            projected=collection_context(task)
            self.assertLessEqual(len(encode(projected)),28000)
            self.assertEqual(reply(projected)['content'],result['content'])

    def test_accumulated_associations_do_not_displace_source_read(self):
        with TemporaryDirectory() as root:
            task=prepared(root)
            original=task.bundle['plan'][0]
            task.bundle['plan']=[{**original,'id':'need_'+str(i),'condition':'Precise source condition '+str(i)} for i in range(6)]
            page=task.bundle['pages'][URL]
            body='A substantive legal paragraph. '*150
            page['content']=body;page['documents']=[]
            task.execute('record_excerpt',{'url':URL,'start_char':0,'end_char':100,
                'need_ids':[n['id'] for n in task.bundle['plan']]},'')
            args={'url':URL,'max_chars':4000}
            result=task.execute('read_document',args,'')
            group(task,args,result)
            task.bundle['messages'][0]['content']='Immutable acquisition instructions. '*280
            projected=collection_context(task)
            self.assertLessEqual(len(encode(projected)),28000)
            self.assertEqual(reply(projected)['content'],result['content'])

    def test_agent_absence_claim_separated_from_saved_inventory(self):
        with TemporaryDirectory() as root:
            task=prepared(root)
            body=task.bundle['pages'][URL]['content']
            task.execute('record_excerpt',{'url':URL,'start_char':0,'end_char':min(100,len(body)),'need_ids':['n']},'')
            result=task.execute('finish_collection',{'gaps':['No direct evidence found for n']},'')
            self.assertEqual(result['acquisition_inventory']['needs'][0]['material_state'],'excerpt_saved')
            self.assertEqual(result['agent_declared_gaps'],['No direct evidence found for n'])
            self.assertNotIn('No direct evidence found for n',result['gaps'])
            self.assertFalse(result['acquisition_complete'])
            self.assertFalse(result['acquisition_inventory']['needs'][0]['semantic_adequacy_verified'])

    def test_useful_deferral_preserves_plan_and_cannot_remove_critical(self):
        with TemporaryDirectory() as root:
            task=prepared(root)
            task.bundle['plan'].append({**task.bundle['plan'][0],'id':'background','priority':'useful'})
            frozen=json.dumps(task.bundle['plan'])
            task.execute('set_acquisition_need_status',{'need_id':'background','status':'deferred',
                'reason':'Background is optional and the current acquisition budget prioritizes the core material.'},'')
            self.assertEqual(json.dumps(task.bundle['plan']),frozen)
            self.assertEqual(inventory(task.bundle)['needs'][1]['acquisition_status'],'deferred')
            with self.assertRaises(ContractError):
                task.execute('set_acquisition_need_status',{'need_id':'n','status':'deferred',
                    'reason':'The critical source has not yet published its document.'},'')

    def test_long_passage_keeps_nearby_heading_and_exact_coordinates(self):
        text='intro '*130+'\n\n# August 2026\n\n'+'Target event details. '*80
        result=find_passages({URL:{'content':text}},{'url':URL,'query':'Target event','limit':1})
        passage=result['passages'][0]; coords=passage['excerpt_args']
        self.assertIn('# August 2026',passage['content'])
        self.assertIn('Target event',passage['content'])
        self.assertEqual(text[coords['start_char']:coords['end_char']],passage['content'])
