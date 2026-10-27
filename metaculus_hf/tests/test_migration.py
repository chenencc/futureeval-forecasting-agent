import unittest
from copy import deepcopy
from metaculus_agent.migration import convert,TIMERS,SERVICES

class MigrationTests(unittest.TestCase):
    def inputs(self):
        campaign=dict(schema='official-competition-v1',tasks={'1':dict(stage='accepted',post_id='11',collection_executions=2),'2':dict(stage='deadline_missed',post_id='12',collection_executions=3)})
        units={name:dict(ActiveState='inactive',UnitFileState='disabled',MainPID='0') for name in TIMERS|SERVICES}
        return campaign,dict(seen_question_ids=[1,2]),{'2026-10-27':{'model':17,'search':6}},dict(units=units,last_model_at=1234)
    def convert(self,values=None,receipt=None):return convert(*(values or self.inputs()),archive_receipt=receipt or dict(verified=True,revision='exact-revision',manifest_sha256='a'*64))
    def test_terminal_tasks_and_prior_reservations_survive(self):
        args=self.inputs();before=deepcopy(args);state,budget=self.convert(args)
        self.assertEqual(args,before);self.assertTrue(state['migration_complete']);self.assertEqual(len(state['tasks']),2)
        self.assertTrue(all(t['status']=='legacy_terminal' for t in state['tasks'].values()))
        self.assertEqual(state['tasks']['1']['legacy_collection_executions'],2);self.assertEqual(budget['days']['2026-10-27'],{'model':17,'search':6})
        self.assertFalse(budget['migration']['budgets_reset'])
    def test_running_task_blocks_transfer(self):
        args=self.inputs();args[0]['tasks']['1']['stage']='analyzing'
        with self.assertRaisesRegex(ValueError,'NATIVE_NONTERMINAL_TASK'):self.convert(args)
    def test_enabled_timer_or_running_service_blocks_transfer(self):
        for name in TIMERS|SERVICES:
            args=self.inputs();args[3]['units'][name]['ActiveState']='active'
            with self.assertRaises(ValueError):self.convert(args)
        args=self.inputs();args[3]['units'][next(iter(SERVICES))]['MainPID']='123'
        with self.assertRaises(ValueError):self.convert(args)
    def test_unverified_archive_or_negative_counter_is_not_imported(self):
        with self.assertRaises(ValueError):self.convert(receipt=dict(verified=False))
        args=self.inputs();args[2]['2026-10-27']['model']=-1
        with self.assertRaises(ValueError):self.convert(args)

if __name__=='__main__':unittest.main()
