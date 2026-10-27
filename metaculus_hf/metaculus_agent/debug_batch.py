"""Three unused historical questions in one hosted job; read-only size accounting."""
import argparse,contextlib,io,json,os,re,tempfile,time
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch
from ForecastAgent.competition import platform
from ForecastAgent.competition.queue import questions,utc
from pipeline_v2.routing import configure_credentials
from .storage import Store
from .platform_adapter import live_request
from .runner import blind_post
from . import debug_sequence as sequence

EXCLUDE={'46432','46431','46430'}

class SelectionError(RuntimeError):
    def __init__(self,code,status=None,retry_after=None):
        super().__init__(code);self.code=code;self.http_status=status;self.retry_after=retry_after;self.phase='historical_question_selection'

def old_post(native,pid,*,sleep=time.sleep):
    for attempt in range(3):
        try:return native.post(pid)
        except HTTPError as exc:
            if exc.code!=429:raise
            from email.utils import parsedate_to_datetime
            value=exc.headers.get('Retry-After') if exc.headers else None
            try:delay=max(0,int(value)) if value and value.isdecimal() else max(0,(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()) if value else 60
            except (ValueError,TypeError):delay=60
            if attempt==2 or delay>120:raise SelectionError('METACULUS_SELECTION_RATE_LIMIT',429,delay) from None
            sleep(max(1,delay))

def known_ids(backend):
    used=set(EXCLUDE);keys=['metaculus/_control/checkpoint.json']
    for folder,suffix in [('metaculus/_debug','/_control/checkpoint.json'),('metaculus/_acceptance','/checkpoint.json')]:
        try:entries=list(backend.api.list_repo_tree(backend.repo_id,folder,recursive=False,revision=backend.head,repo_type='dataset'))
        except Exception as exc:
            if getattr(getattr(exc,'response',None),'status_code',None)==404:continue
            raise
        if len(entries)>100:raise RuntimeError('DEBUG_HISTORY_SCAN_BOUND')
        keys.extend(x.path+suffix for x in entries if not hasattr(x,'size'))
    for key in keys:
        if backend.exists(key):used.update(str(x).split(':')[-1] for x in json.loads(backend.read(key)).get('tasks',{}))
    return used

def select(native,used,*,clock=None,candidate_posts=range(46202,46142,-1),sleep=time.sleep):
    """Only old, unused, unforecasted leaves; no outcome labels enter inputs."""
    clock=clock or datetime.now(timezone.utc);selected=[];skips=defaultdict(int)
    for pid in candidate_posts:
        # The production reader shares the platform account's rate window.
        sleep(1.1)
        try:post=old_post(native,pid,sleep=sleep)
        except HTTPError as exc:
            if exc.code==404:skips['not_found']+=1;continue
            raise
        created=post.get('created_at')
        if not created or utc(created)>clock-timedelta(hours=24):skips['not_old']+=1;continue
        for q in questions(post):
            qid=str(q['id'])
            if qid in used or qid in EXCLUDE:skips['previously_used']+=1;continue
            if q.get('type') not in {'binary','numeric','discrete','date','multiple_choice'}:continue
            try:already=platform.has_existing_forecast(q)
            except RuntimeError:skips['own_history_unreadable']+=1;continue
            if already:skips['already_forecasted']+=1;continue
            clean=blind_post(post,q)
            try:live_request(clean,clean['question'])
            except (ValueError,KeyError,TypeError):skips['unsupported_metadata']+=1;continue
            selected.append(dict(post_id=str(post['id']),question_id=qid,title=q.get('title') or post.get('title'),question_type=q['type'],created_at=created))
            used.add(qid);break  # one distinct parent post per case
        if len(selected)==3:return selected,dict(skips)
    raise RuntimeError('THREE_UNUSED_HISTORICAL_QUESTIONS_NOT_FOUND')

def sizes(backend,session,reports):
    base='metaculus/_debug/'+session;revision=backend.head
    files={x.path:x.size for x in backend.api.list_repo_tree(backend.repo_id,base,recursive=True,revision=revision,repo_type='dataset') if hasattr(x,'size')}
    owners=defaultdict(set);rows=[]
    for report in reports:
        qid=report['question_id'];pre=report['hf_prefix']
        archive=json.loads(backend.read(pre+'/question-runtime.json',revision))
        hashes=set(archive['runtime'].values())
        for h in hashes:owners[h].add(qid)
        direct={k:v for k,v in files.items() if k.startswith(pre+'/') or k.startswith(base+'/case-'+str(report['case_index'])+'/')}
        rows.append(dict(case_index=report['case_index'],question_id=qid,title=report['question_title'],context_bytes=files[pre+'/context.parquet'],analysis_bytes=files[pre+'/analysis.parquet'],direct_files_bytes=sum(direct.values()),runtime_hashes=sorted(hashes)))
    for row in rows:
        hashes=row.pop('runtime_hashes');owned=[h for h in hashes if len(owners[h])==1];shared=[h for h in hashes if len(owners[h])>1]
        row['exclusive_runtime_bytes']=sum(files[base+'/_control/objects/'+h] for h in owned)
        row['shared_runtime_referenced_bytes']=sum(files[base+'/_control/objects/'+h] for h in shared)
        row['exclusive_total_bytes']=row['direct_files_bytes']+row['exclusive_runtime_bytes']
    total=sum(files.values());exclusive=sum(r['exclusive_total_bytes'] for r in rows)
    return dict(schema='metaculus-debug-space/1',revision=revision,cases=rows,session_current_files=len(files),session_current_bytes=total,
                session_shared_control_and_history_bytes=total-exclusive,physical_billing_claimed=False,
                measurement='Current-revision file byte sizes; shared hashes counted once. Excludes Git history/compression/physical HF billing.')

def run(backend,root,session,native,*,collector=sequence.collect,analyzer=sequence.analyze,used=None,candidate_posts=range(46202,46142,-1)):
    if not re.fullmatch('[0-9a-f]{32}',session):raise ValueError('DEBUG_SESSION_ID')
    if backend.exists('metaculus/_debug/'+session+'/_control/checkpoint.json'):raise RuntimeError('DEBUG_SESSION_ALREADY_STARTED')
    selected,skips=select(native,set(used) if used is not None else known_ids(backend),candidate_posts=candidate_posts)
    cases=tuple((x['post_id'],x['question_id']) for x in selected);reports=[]
    with patch.object(sequence,'CASES',cases):
        for i in range(1,4):
            report=sequence.batched_case(backend,Path(root)/str(i),session,i,native,collector=collector,analyzer=analyzer)
            reports.append(report)
            if report['status']!='debug_case_passed':return dict(status='debug_batch_stopped',selected=selected,selection_skips=skips,reports=[sequence.public(r) for r in reports],submissions=0)
    usage=sizes(backend,session,reports)
    return dict(status='debug_batch_passed',session=session,selected=selected,selection_skips=skips,reports=[sequence.public(r) for r in reports],
                hf_successful_commits=sum(r['hf_successful_commits'] for r in reports),storage=usage,submissions=0)

def main():
    p=argparse.ArgumentParser();p.add_argument('--session',required=True);args=p.parse_args();configure_credentials()
    backend=Store(os.environ.get('HF_DATASET','chchen13/forecastagent-data'),os.environ['HF_TOKEN']);native=platform.Client(os.environ['METACULUS_TOKEN']);original=native.request
    def readonly(method,path,data=None):
        if method!='GET':raise RuntimeError('DEBUG_SUBMISSION_FORBIDDEN')
        return original(method,path,data)
    with tempfile.TemporaryDirectory(prefix='metaculus-three-unused-') as root,patch.object(native,'request',readonly):return run(backend,root,args.session,native)

if __name__=='__main__':
    try:
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):result=main()
        print(json.dumps(result));raise SystemExit(0 if result['status']=='debug_batch_passed' else 1)
    except Exception as exc:
        code=getattr(exc,'code',None) or (str(exc) if re.fullmatch('[A-Z][A-Z0-9_]{1,99}',str(exc)) else type(exc).__name__)
        print(json.dumps(dict(status='debug_batch_blocked',code=code,http_status=getattr(exc,'http_status',None),phase=getattr(exc,'phase',None),retry_after=getattr(exc,'retry_after',None))));raise SystemExit(1)
