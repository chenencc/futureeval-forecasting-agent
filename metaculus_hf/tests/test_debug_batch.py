import copy,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from metaculus_agent.debug_batch import select,run,known_ids,EXCLUDE,old_post,SelectionError,resume_stopped
from urllib.error import HTTPError
from test_runner import post
from test_debug_sequence import seed,capture,analyze

class Native:
    def __init__(self):
        self.posts={};self.calls=[]
        for i in range(6):
            pid=46202-i;qid=46429-i;p=post(ident=qid);p['id']=pid
            p['question'].update(status='closed',scheduled_close_time='2026-01-01T00:00:00Z',spot_scoring_time='2026-01-01T00:00:00Z')
            self.posts[str(pid)]=p
    def post(self,pid):self.calls.append(str(pid));return copy.deepcopy(self.posts[str(pid)])
    def account(self):return {'id':309450}

class Api:
    def __init__(self,backend):self.backend=backend
    def repo_info(self,*a,**kw):return SimpleNamespace(sha=self.backend.head,private=True)
    def file_exists(self,repo,key,**kw):return key in self.backend.history[kw.get('revision',self.backend.head)]
    def list_repo_tree(self,repo,folder,*,recursive=False,revision=None,**kw):
        files=self.backend.history[revision or self.backend.head];rows=[];dirs=set()
        for key,raw in files.items():
            if not key.startswith(folder+'/'):continue
            rest=key[len(folder)+1:]
            if recursive or '/' not in rest:rows.append(SimpleNamespace(path=key,size=len(raw)))
            else:dirs.add(folder+'/'+rest.split('/')[0])
        return rows+[SimpleNamespace(path=d) for d in sorted(dirs)]

class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        env=patch.dict(os.environ,dict(os.environ));env.start();self.addCleanup(env.stop)
        self.backend=seed();self.backend.api=Api(self.backend);self.backend.repo_id='fixture/dataset'
        # Skip only fixed pacing in fixtures; explicit 429 tests inject their own clock.
        pacing=patch('metaculus_agent.debug_batch.select',side_effect=lambda *a,**kw:select(*a,**kw,sleep=lambda _:None));pacing.start();self.addCleanup(pacing.stop)
    def test_selection_excludes_previously_used_and_existing_actual_forecasts(self):
        native=Native();native.posts['46201']['question']['my_forecasts']={'latest':{'author_id':309450},'history':[]}
        rows,skips=select(native,{'46429'},candidate_posts=range(46202,46196,-1),sleep=lambda _:None)
        self.assertEqual([r['question_id'] for r in rows],['46427','46426','46425'])
        self.assertEqual(skips['previously_used'],1);self.assertEqual(skips['already_forecasted'],1)
        self.assertTrue(all(r['question_id'] not in EXCLUDE for r in rows))
    def test_all_debug_and_acceptance_tasks_join_production_exclusions(self):
        self.backend.publish({'metaculus/_debug/'+'a'*32+'/_control/checkpoint.json':json.dumps({'tasks':{'111':{}}}).encode(),
                              'metaculus/_acceptance/'+'b'*32+'/checkpoint.json':json.dumps({'tasks':{'222':{}}}).encode()})
        self.assertTrue({'111','222',*EXCLUDE}.issubset(known_ids(self.backend)))
    def test_one_process_three_unused_cases_one_commit_each_and_exact_size_partition(self):
        start=int(self.backend.head);production=self.backend.read('metaculus/_control/checkpoint.json');session='a'*32
        report=run(self.backend,self.root,session,Native(),collector=capture,analyzer=analyze,used=set(),candidate_posts=range(46202,46199,-1))
        self.assertEqual(report['status'],'debug_batch_passed',report);self.assertEqual(report['hf_successful_commits'],3)
        self.assertEqual(int(self.backend.head)-start,3);self.assertEqual(report['submissions'],0)
        self.assertEqual(self.backend.read('metaculus/_control/checkpoint.json'),production)
        self.assertEqual([r['completed_cases'] for r in report['reports']],[[1],[1,2],[1,2,3]])
        space=report['storage'];self.assertEqual(len(space['cases']),3)
        self.assertEqual(sum(r['exclusive_total_bytes'] for r in space['cases'])+space['session_shared_control_and_history_bytes'],space['session_current_bytes'])
        total=sum(len(v) for k,v in self.backend.history[self.backend.head].items() if k.startswith('metaculus/_debug/'+session+'/'))
        self.assertEqual(space['session_current_bytes'],total)
        self.assertTrue(all(r['context_bytes']>0 and r['analysis_bytes']>0 for r in space['cases']))
    def test_first_integrity_failure_stops_before_next_question_collection(self):
        seen=[]
        def fail(task,*args):seen.append(task['id']);raise ValueError('BODY_HASH_MISMATCH')
        start=int(self.backend.head)
        report=run(self.backend,self.root,'b'*32,Native(),collector=fail,analyzer=analyze,used=set(),candidate_posts=range(46202,46199,-1))
        self.assertEqual(report['status'],'debug_batch_stopped');self.assertEqual(seen,['46429'])
        self.assertEqual(int(self.backend.head)-start,1);self.assertEqual(len(report['reports']),1)
    def test_existing_session_cannot_recollect(self):
        session='c'*32;self.backend.publish({'metaculus/_debug/'+session+'/_control/checkpoint.json':b'{}'});native=Native()
        with self.assertRaisesRegex(RuntimeError,'DEBUG_SESSION_ALREADY_STARTED'):run(self.backend,self.root,session,native)
        self.assertEqual(native.calls,[])
    def test_recent_or_unreadable_history_question_is_not_called_unused(self):
        native=Native();native.posts['46202']['created_at']='2099-01-01T00:00:00Z';native.posts['46201']['question'].pop('my_forecasts')
        rows,skips=select(native,set(),candidate_posts=range(46202,46197,-1),sleep=lambda _:None)
        self.assertEqual(len(rows),3);self.assertEqual(skips['not_old'],1);self.assertEqual(skips['own_history_unreadable'],1)
    def test_relaxed_debug_uses_first_three_old_posts_even_with_prior_forecasts(self):
        native=Native()
        for p in native.posts.values():p['question']['my_forecasts']={'latest':{'author_id':309450},'history':[]}
        rows,skips=select(native,{'46429','46428','46427'},candidate_posts=range(46202,46199,-1),sleep=lambda _:None,require_unused=False)
        self.assertEqual([r['question_id'] for r in rows],['46429','46428','46427'])
        self.assertEqual(len(native.calls),3);self.assertTrue(all(r['question_id'] not in EXCLUDE for r in rows))
        self.assertEqual(skips,{})
    def test_429_honors_retry_after_on_same_read_and_never_calls_models(self):
        calls=[];waits=[]
        class Limited:
            def post(self,pid):
                calls.append(pid)
                if len(calls)==1:raise HTTPError('https://www.metaculus.com/api/posts/1/',429,'fixture',{'Retry-After':'7'},None)
                return {'id':pid}
        self.assertEqual(old_post(Limited(),1,sleep=waits.append),{'id':1});self.assertEqual(calls,[1,1]);self.assertEqual(waits,[7])
    def test_persistent_or_excessive_rate_wait_stops_with_safe_details(self):
        class Limited:
            def post(self,pid):raise HTTPError('https://www.metaculus.com/api/posts/1/',429,'fixture',{'Retry-After':'999'},None)
        waits=[]
        with self.assertRaises(SelectionError) as caught:old_post(Limited(),1,sleep=waits.append)
        self.assertEqual(waits,[]);self.assertEqual(caught.exception.http_status,429);self.assertEqual(caught.exception.retry_after,999)
        self.assertEqual(caught.exception.phase,'historical_question_selection')
    def test_resume_failed_analysis_probes_default_then_continues_third_without_recollecting(self):
        session='d'*32
        def fail_second(value,folder,checkpoint):
            if value['question_id']=='metaculus:46428':raise RuntimeError('SAVED_SECOND_ANALYSIS_FAILURE')
            return analyze(value,folder,checkpoint)
        stopped=run(self.backend,self.root/'first',session,Native(),collector=capture,analyzer=fail_second,used=set(),candidate_posts=range(46202,46199,-1))
        self.assertEqual(stopped['status'],'debug_batch_stopped');seen=[]
        def only_third(task,*args):
            seen.append(task['id']);self.assertEqual(task['id'],'46427');return capture(task,*args)
        before=int(self.backend.head)
        passed=resume_stopped(self.backend,self.root/'resume',session,Native(),collector=only_third,analyzer=analyze,probe_default=True)
        self.assertEqual(passed['status'],'debug_batch_passed',passed);self.assertEqual(seen,['46427'])
        self.assertEqual(int(self.backend.head)-before,2);self.assertEqual(passed['new_hf_successful_commits'],2)
        second=passed['reports'][1];self.assertEqual(second['new_search_requests'],0);self.assertEqual(second['new_fetch_requests'],0)
        self.assertTrue(second['reused_prior_pass'] is False);self.assertEqual(second['operational_default_probe']['provider_calls'],0)
    def test_resume_requires_a_saved_failed_analysis_and_passed_predecessor(self):
        with self.assertRaises(Exception):resume_stopped(self.backend,self.root,'e'*32,Native())

if __name__=='__main__':unittest.main()
