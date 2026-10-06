"""Separate tournament controls and bounded shared public dispatch."""
import os
import unittest
from unittest.mock import patch
from ForecastAgent.infrastructure.test_storage_monitor import watch


class MiniBenchMonitorTests(unittest.TestCase):
    def test_group_children_are_not_limited_to_list_preview(self):
        children=[{'id':i,'status':'open','scheduled_close_time':'2099-01-01T00:00:00Z'} for i in range(1,6)]
        def get(url):
            if '/posts/42/' in url:return {'id':42,'group_of_questions':{'questions':children}}
            return {'results':[{'id':42,'group_of_questions':{'questions':children[:3]}}],'next':None}
        self.assertEqual(watch.open_ids('unused',get,tournament='minibench'),{'1','2','3','4','5'})

    def test_minibench_uses_current_slug_and_completes_empty_page(self):
        urls=[]
        def get(url):
            urls.append(url);return {'results':[],'next':url+'&offset=100'}
        self.assertEqual(watch.open_ids('unused',get,tournament='minibench'),set())
        self.assertEqual(len(urls),1);self.assertIn('tournaments=minibench',urls[0])

    def test_dispatches_at_most_one_profile(self):
        calls=[]
        def run(**profile):
            calls.append(profile);return {'dispatched':True,'open_question_count':2,'reason':'new_questions'}
        with patch.dict(os.environ,{'MINIBENCH_LISTEN_ENABLED':'true'}),patch.object(watch,'run',run):
            report=watch.run_all()
        self.assertEqual(len(calls),1);self.assertTrue(report['dispatched'])
        self.assertEqual(set(report['tournaments']),{'fall-futureeval-2026','minibench'})

    def test_idle_profiles_use_distinct_recovery_artifacts(self):
        calls=[]
        def run(**profile):
            calls.append(profile);return {'dispatched':False,'open_question_count':0,'reason':'idle'}
        with patch.dict(os.environ,{'MINIBENCH_LISTEN_ENABLED':'true'}),patch.object(watch,'run',run):
            watch.run_all()
        self.assertEqual({p['artifact_prefix'] for p in calls},{'futureeval-official','minibench-official'})
        self.assertEqual({p['worker'] for p in calls},{'official_competition.yaml','minibench_competition.yaml'})

    def test_one_failed_profile_does_not_suppress_other(self):
        def run(**profile):
            if profile['tournament']=='minibench':raise ValueError('Bad control')
            return {'dispatched':True,'open_question_count':1,'reason':'new_questions'}
        with patch.dict(os.environ,{'MINIBENCH_LISTEN_ENABLED':'true'}),patch.object(watch,'run',run),patch.object(watch,'now') as clock:
            clock.return_value.timestamp.return_value=600
            clock.return_value.isoformat.return_value='2026-10-06T00:00:00Z'
            report=watch.run_all()
        self.assertEqual(report['status'],'failed');self.assertTrue(report['dispatched'])


if __name__=='__main__':unittest.main()
