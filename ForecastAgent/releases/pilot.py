"""Bounded release provider validation without platform delivery or credentials."""
import argparse
import copy
import json
import os
import sys
from pathlib import Path
from ForecastAgent.competition.queue import load, save, digest
from ForecastAgent.releases import v1_0_2 as release
from ForecastAgent.releases.guard import execute
from ForecastAgent.tests.test_competition_mercury import bundle

ROOT=Path(__file__).resolve().parents[2]


def request_for(kind):
    if kind=='binary':
        row=load(ROOT/'ForecastAgent/fixtures/raw_collection_2026_100.json')[0]
        request={k:copy.deepcopy(row[k]) for k in ('id','question','resolution_criteria','fine_print','background') if k in row}
        request['question_type']='binary'
    elif kind=='date':
        request=bundle('date')['request'];request['id']='900000002'
        request['question']='When will the hypothetical project deliver its first public release?'
        request['resolution_criteria']='Resolve to the first public release date, or above the upper bound if it has not occurred by January 15, 2027.'
        request['fine_print']='Synthetic format and real provider compatibility test; not a historical accuracy test.'
    else:
        ident={'numeric':45082,'multiple_choice':44151,'discrete':43945}[kind]
        row=next(r for r in load(ROOT/'ForecastAgent/fixtures/nonbinary-resolved-core20-inputs.json') if r.get('question_id')==ident)
        request={'id':str(ident),'question':row['title'],'question_type':kind,'resolution_criteria':row['criteria'],
                 'fine_print':'','background':'Archived platform metadata; current retrieval is an engineering test.'}
        request.update({k:copy.deepcopy(row[k]) for k in ('options','scaling','inbound_outcome_count','open_lower_bound','open_upper_bound')})
    request.update(mode='live',pipeline='collection',acquisition_profile='collection_v3',official_competition=True,
                   acquisition_strategy='intelligent_materials_v3',drain_unseen_reads_before_stall=True,exa_search_policy='required')
    return request


def child(root,kind):
    root=Path(root);request=request_for(kind);identity={'release':'1.0.2','kind':kind,'request_sha256':digest(request)}
    if (root/'identity.json').exists() and load(root/'identity.json')!=identity:raise ValueError('Frozen pilot identity changed')
    save(root/'identity.json',identity)
    if (root/'result.json').exists():
        release.verify_candidate(root);return load(root/'result.json')
    if kind=='date':
        package=bundle('date');package['request']=request
        package['pages']={'https://example.org/hypothetical-project':{'content':
            'Synthetic bulletin, October 6, 2026. The release candidate is complete. Public launch is expected in November 2026. External review may postpone launch. '*40,
            'body_diagnostics':{'usable_text':True}}}
        source=root/'analysis-input.json';save(source,package)
    else:
        adapter=release.collect(request,root/'retrieval')
        if adapter.get('result',{}).get('incomplete'):raise RuntimeError('Acquisition interrupted; exact native ledgers preserved')
        source=root/'analysis-input.json'
        if not source.exists():save(source,release.supplement(adapter,root,request['id']))
    result=release.analyze(source,root,request['id']);release.verify_candidate(root)
    if result!=release.analyze(source,root,request['id']):raise ValueError('Durable candidate replay changed')
    report={'release_version':'1.0.2','kind':kind,'question_id':request['id'],'status':'scored',
            'payload':result['payload'],'selection':result['selection'],
            'evidence_scope':'synthetic saved material; real provider' if kind=='date' else 'fresh collection and independent supplement; real providers',
            'real_platform_posts':0,'no_forecasts_submitted':True,'replayed_same_candidate':True,
            'readable_page_count':sum(bool(p.get('content') and p.get('body_diagnostics',{}).get('usable_text')) for p in load(source).get('pages',{}).values())}
    save(root/'result.json',report);return report


def run(root,kind,seconds=1500):
    root=Path(root).resolve()
    report=execute([sys.executable,'-u','-m','ForecastAgent.releases.pilot','--root',str(root),'--kind',kind,'--child'],
                   root/'child.log',seconds,cwd=str(ROOT))
    report.update(kind=kind,no_forecasts_submitted=True,preserved_state=True);save(root/'watchdog.json',report)
    if report['status']!='completed' or not (root/'result.json').exists():
        save(root/'failure.json',{'kind':kind,'status':report['status'],'no_budget_reset':True,'preserved_state':True})
        raise RuntimeError('Bounded pilot did not score; preserve artifact and resume exact state')
    return load(root/'result.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--kind',choices=['binary','multiple_choice','numeric','date','discrete'],required=True)
    p.add_argument('--child',action='store_true');args=p.parse_args();os.environ.pop('METACULUS_TOKEN',None)
    print(json.dumps(child(args.root,args.kind) if args.child else run(args.root,args.kind)))
