"""Offline queue load test using real Mercury journals and simulated transports."""
import argparse
import copy
import os
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from ForecastAgent.competition import platform
from ForecastAgent.competition.queue import load, save, questions, utc
from ForecastAgent.providers.decisions import validate
from ForecastAgent.releases import surfaces, v1_0_2 as release
from ForecastAgent.tests.test_competition_mercury import bundle, response

KINDS=('binary','multiple_choice','numeric','date','discrete')


def question(ident,kind):
    request=bundle(kind)['request']
    result={k:copy.deepcopy(v) for k,v in request.items() if k in
            ('resolution_criteria','fine_print','options','scaling','inbound_outcome_count',
             'open_lower_bound','open_upper_bound','unit')}
    if kind=='discrete':result['inbound_outcome_count']=31
    if kind=='numeric' and ident%2:result['scaling'].update(range_min=1.,zero_point=0.)
    if kind in {'numeric','date','discrete'} and ident%3==0:
        result.update(open_lower_bound=False,open_upper_bound=False)
    result.update(id=ident,type=kind,status='open',title=f'Offline format event {ident}',
                  my_forecasts={'latest':None,'history':[]},
                  scheduled_close_time=(utc()+timedelta(days=1,seconds=ident)).isoformat())
    return result


class Platform:
    """Simulate the HTTP boundary only; run the real atomic delivery/readback code."""
    def __init__(self,documents):self.documents=documents;self.writes=[];self.private=[];self.lose_response=False
    def account(self):return {'id':platform.BOT_ID,'api_forecasting_access':'enabled'}
    def post(self,ident):return copy.deepcopy(self.documents[str(ident)])
    def comments(self,ident):return [c for c in self.private if c['on_post']==int(ident)]
    def request(self,method,endpoint,body):
        assert method=='POST' and endpoint=='questions/bulk-forecast-comment/'
        candidate=body['forecasts'][0];post_id=str(body['comments'][0]['on_post'])
        post=surfaces.normalize(self.documents[post_id])
        q=next(q for q in questions(post) if q['id']==candidate['question'])
        if 'probability_yes' in candidate:values=[1-candidate['probability_yes'],candidate['probability_yes']]
        elif 'continuous_cdf' in candidate:values=candidate['continuous_cdf']
        else:values=[candidate['probability_yes_per_category'].get(o) for o in (q.get('all_options_ever') or q['options'])]
        own={'latest':{'author_id':platform.BOT_ID,'forecast_values':values,'start_time':utc().timestamp()},'history':[]}
        raw=self.documents[post_id]
        raw_questions=questions(raw)+[v for k,v in (raw.get('conditional') or {}).items() if k in {'question_yes','question_no'}]
        next(x for x in raw_questions if x['id']==q['id'])['my_forecasts']=own
        self.writes.append(copy.deepcopy(body))
        self.private.append({**body['comments'][0],'id':len(self.private)+1,'author':{'id':platform.BOT_ID}})
        if self.lose_response:raise TimeoutError('Simulated response lost after acceptance')
        return {'status':201,'data':{}}


def snapshot(root,documents):
    rows=[]
    for ident,post in documents.items():
        save(root/f'{ident}.json',{'post':post})
        for q in questions(surfaces.normalize(post)):
            rows.append({'question_id':q['id'],'post_id':post['id'],'open':q['status']=='open'})
    save(root/'index.json',{'tournament':'fall-futureeval-2026','open_scan_complete':True,
                           'retrieved_at_utc':utc().isoformat(),'questions':rows,'open_question_count':len(rows)})
    return root


def saved_collector(request,folder):
    """A saved material fixture, not a simulated claim about retrieval quality."""
    raw=bundle(request['question_type']);raw['request']=request
    path=folder/'release-1.0.2/package.json';save(path,raw)
    adapter={'request':request,'result':{'incomplete':False,'status':'raw_package_exported'},
             'release_acquisition':{'version':release.VERSION,'package_sha256':release.file_hash(path)}}
    save(folder/'bundle.json',adapter)
    return adapter


def decide(state,registry,key,observer):
    record={'request':{'state':state,'questions':registry},'status':'reserved'}
    token=observer('reserve',record);answer=validate(response(registry),registry)
    record.update(status='received',response=answer);observer('complete',record,token)
    return answer


def run(root,count=100):
    root=Path(root)
    if (root/'stress-report.json').exists():return load(root/'stress-report.json')
    if (root/'worker').exists():raise ValueError('Incomplete stress run exists; preserve it for investigation')
    docs={str(10000+i):{'id':10000+i,'title':f'Post {i}','user_permission':'forecaster',
                       'question':question(i,KINDS[(i-1)%5])} for i in range(1,count+1)}
    client=Platform(docs);incoming=snapshot(root/'incoming',docs);start=time.monotonic();dispatches=0
    with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline'}),patch('ForecastAgent.providers.decisions.decide',decide):
        while dispatches<(count+4)//5:
            report=release.once(root/'worker',incoming,enabled=True,client=client,collector=saved_collector,limit=5)
            dispatches+=1
        before=len(client.writes)
        again=release.once(root/'worker',incoming,enabled=True,client=client,collector=saved_collector,limit=5)
    state=load(root/'worker/campaign.json')
    journals=list((root/'worker').rglob('record.json'))
    candidates=list((root/'worker/tasks').glob('*/candidate.json'))
    for path in candidates:release.verify_candidate(path.parent)
    assert len(candidates)==count and len(client.writes)==count and before==len(client.writes)
    assert all(t['stage']=='accepted' for t in state['tasks'].values()) and not again['processed_ids']
    result={'release_version':release.VERSION,'mode':'offline simulated HTTP; real journals, analysis and delivery adapters',
            'question_count':count,'valid_candidates':len(candidates),'type_distribution':{k:sum(q['question']['type']==k for q in docs.values()) for k in KINDS},
            'dispatch_count':dispatches,'state_distribution':report['state_distribution'],
            'simulated_atomic_posts':len(client.writes),'real_platform_posts':0,'real_model_http_attempts':0,
            'duplicate_posts_on_replay':len(client.writes)-before,'integrity_verified_candidates':len(candidates),
            'elapsed_seconds':time.monotonic()-start,'all_questions_accounted':len(state['tasks'])==count}
    save(root/'stress-report.json',result);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--count',type=int,default=100);args=parser.parse_args()
    print(__import__('json').dumps(run(args.root,args.count)))
