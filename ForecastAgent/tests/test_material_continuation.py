"""Bounded material continuation and novel cohort dispatch contracts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.runtime.retrieval import RetrievalTask, COLLECTION_TOOLS
from ForecastAgent.runtime.collection_actions import material_read_action, raw_stop_reason, no_progress_limit
from ForecastAgent.runtime.tool_selection import active_tools
from ForecastAgent.runtime.capacity import SOLID, SOLID_V2, freeze
from ForecastAgent.tests.test_material_recovery import dataset_bundle
from ForecastAgent.supplement import enhanced,frontier
from ForecastAgent.experiments.solid_collection import run,question,COHORTS,case_indices
from ForecastAgent.analysis.pilot import load


class MaterialContinuationTests(unittest.TestCase):
    def test_partial_invalid_batch_preserves_valid_child_without_guessing(self):
        from ForecastAgent.runtime.contracts import partition_source_batch,ContractError
        with tempfile.TemporaryDirectory() as tmp:
            task,parent=self.task(tmp)
            tools=active_tools(task,COLLECTION_TOOLS,'read_sources')
            # Raw acquisition removes excerpt-query requirements in the runtime.
            for entry in tools:
                schema=entry['function']['parameters']
                schema['required']=[x for x in schema.get('required',[]) if x!='queries']
                schema['properties'].pop('queries',None)
            args,rejected=partition_source_batch(task,
                {'urls':[parent,'https://invented.example/target.csv']},tools)
            self.assertEqual(args['urls'],[parent])
            self.assertEqual(len(rejected),1)
            self.assertFalse(rejected[0]['network_attempted'])
            self.assertEqual(task.bundle['fetch_attempts'],[])
            with self.assertRaises(ContractError):
                partition_source_batch(task,{'urls':['https://invented.example/target.csv']},tools)
            with self.assertRaises(ContractError):
                partition_source_batch(task,{'urls':[parent]*5},tools)

    def task(self,tmp):
        fixture=dataset_bundle(); q=fixture['request']
        q.update(mode='live',pipeline='collection',acquisition_profile='collection_v3',
            acquisition_focus='raw_recall',collection_temporal_policy='current_information',budget_profile='solid_v2',exa_search_policy='optional')
        task=RetrievalTask(Path(tmp),q)
        task.bundle['plan']=[]
        parent=next(iter(fixture['pages']));task.store_page(parent,fixture['pages'][parent])
        return task,parent

    def test_pending_material_extends_stall_once_without_quota_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            task,parent=self.task(tmp)
            task.bundle['control']['no_progress_turns']=2
            self.assertEqual(no_progress_limit(task),4)
            self.assertIsNone(raw_stop_reason(task))
            action=material_read_action(task)
            self.assertIn(parent+'/down.txt',action['urls'])
            task.bundle['control']['no_progress_turns']=4
            self.assertEqual(raw_stop_reason(task),'raw_no_progress_limit')
            task.bundle['fetch_attempts']=[{'url':u,'status':'reserved'} for u in action['urls']]
            self.assertIsNone(material_read_action(task))
            self.assertEqual(no_progress_limit(task),2)
            self.assertEqual(len(task.bundle['fetch_attempts']),len(action['urls']))

    def test_stalled_forced_read_binds_only_pending_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            task,parent=self.task(tmp);task.bundle['control']['no_progress_turns']=2
            names=[t['function']['name'] for t in active_tools(task,COLLECTION_TOOLS)]
            self.assertNotIn('finish_collection',names)
            tool=active_tools(task,COLLECTION_TOOLS,'read_sources')[0]
            urls=tool['function']['parameters']['properties']['urls']['items']['enum']
            self.assertNotIn(parent,urls)
            self.assertIn(parent+'/down.txt',urls)

    def test_extra_frontier_capacity_is_material_only_and_opt_in(self):
        b=dataset_bundle();parent=next(iter(b['pages']))
        b['pages'][parent]['links']=[parent+f'/measurement-{i}.txt' for i in range(12)]
        q=b['request'];q.update(budget_profile='solid_v2',pipeline='collection')
        routed=frontier.admit(enhanced.plan(b)['sources'],q,pages=b['pages'])
        self.assertEqual(sum(x['origin']=='saved_link' for x in routed['accepted']),8)
        q['budget_profile']='solid_v1'
        routed=frontier.admit(enhanced.plan(b)['sources'],q,pages=b['pages'])
        self.assertEqual(sum(x['origin']=='saved_link' for x in routed['accepted']),4)
        self.assertEqual(frontier.POLICY['linked_total'],16)

    def test_default_quotas_remain_and_old_capacity_cannot_upgrade(self):
        self.assertEqual({k:SOLID_V2[k] for k in SOLID},SOLID)
        with self.assertRaisesRegex(ValueError,'Frozen'):
            freeze({'capacity':dict(SOLID)}, {'budget_profile':'solid_v2','pipeline':'collection','experiment_id':'x'},True)

    def test_third_level_is_material_only_and_restart_does_not_refetch(self):
        b=dataset_bundle();b['pages']={}
        q=b['request'];q.update(budget_profile='solid_v2',pipeline='collection')
        root='https://example.org/station'
        q['resolution_criteria']='Use station 87654321 in cm at 08:00 CEST from '+root
        urls=[root,'https://example.org/archive', 'https://example.org/archive/01.09.2026',
              'https://example.org/archive/01.09.2026/data.txt']
        def fetch(url):
            index=urls.index(url)
            return {'content':'01.09.2026\n87654321\ncm\n07:45#17\n08:00#18\n08:15#19' if index==3 else
                'Download raw data for the last 31 days. Older archive data are available.',
                'links':[] if index==3 else [urls[index+1]]}
        with tempfile.TemporaryDirectory() as tmp,patch.object(enhanced,'fetch_document',side_effect=fetch) as http:
            out=enhanced.run(b,tmp,network=True,max_link_depth=3)
            self.assertIn(urls[-1],out['pages']);self.assertEqual(http.call_count,4)
            enhanced.run(b,tmp,network=True,max_link_depth=3)
            self.assertEqual(http.call_count,4)
            last=next(x for x in load(Path(tmp)/'frontier.json')['candidates'] if x['url']==urls[-1])
            self.assertEqual(last['depth'],3)

    def test_three_acceptance_then_fifteen_disjoint_new_cases(self):
        self.assertEqual(len(COHORTS['repair3']),3);self.assertEqual(len(COHORTS['new15']),15)
        self.assertFalse(set(COHORTS['new15'])&set(COHORTS['pilot5']))
        self.assertEqual(case_indices('new15',2),[6,7,8,9,10])
        self.assertEqual(case_indices('repair3'),[1,2,3])
        with self.assertRaises(ValueError):case_indices('repair3',2)
        with tempfile.TemporaryDirectory() as tmp:
            m=run(1,tmp,'offline-new',True,'new15')
            self.assertEqual(m['question_id'],'43658');self.assertEqual(m['capacity'],SOLID_V2)
            self.assertEqual(m['material_max_depth'],3)
            q=load(Path(tmp)/'question.json')
            self.assertFalse({'resolution','probability','historical_snapshot_bundle'}&set(q))
        for ident in COHORTS['new15']:self.assertEqual(question(ident,'offline')['id'],ident)


if __name__=='__main__':unittest.main()
