"""Frozen-material P0 regression and bounded acquisition-only shadow pilots."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import types
import unittest

from ForecastAgent.market_repricing.common import digest, read, write, now, policy
from ForecastAgent.market_repricing.runtime import activate


def frozen_sources(root):
    index=read(root/'prediction_markets/latest-material-revisions.json')
    for e in index['cases']:
        p=Path(e['result_file']).parent/'frozen-input.json'
        b=read(p)
        if digest(b)!=e['material_sha256']:raise ValueError('Frozen market input changed')
        yield e['key'],p,b
    p=Path('D:/metaculus/.tmp/metaculus-live-review-20261014/selected-evidence/tasks/46290/analysis-input.json')
    yield 'official:46290',p,read(p)


def regression(root, output):
    from ForecastAgent.market_repricing import materials
    code=subprocess.check_output(['git','show','098a56c:ForecastAgent/market_repricing/materials.py'],text=True,encoding='utf-8')
    baseline=types.ModuleType('ForecastAgent.market_repricing._baseline_materials')
    baseline.__package__='ForecastAgent.market_repricing'
    exec(compile(code,'frozen-baseline-materials','exec'),baseline.__dict__)
    records=[]
    for key,path,bundle in frozen_sources(root):
        before=hashlib.sha256(path.read_bytes()).hexdigest();rows=[]
        for url,page in bundle.get('pages',{}).items():
            old=baseline.quality(page,url);new=materials.quality(page,url)
            rows.append({'url':url,'old_usable':old['usable_text'],'new_usable':new['usable_text'],
                'old_full':old.get('qualifies_full_body'),'new_full':new.get('qualifies_full_body'),
                'body_role':new['body_role'],'data_coverage':new['data_coverage']})
        if hashlib.sha256(path.read_bytes()).hexdigest()!=before:raise ValueError('Original snapshot modified')
        records.append({'key':key,'file_sha256':before,'pages':rows})
    rejected=[{'key':r['key'],'url':p['url']} for r in records for p in r['pages'] if p['old_usable'] and not p['new_usable']]
    if rejected:raise ValueError('Unreviewed new body rejections: '+str(rejected))
    report={'schema':'market-p0-regression-v1','checked_at':now(),'cases':records,
        'case_count':len(records),'new_body_rejections':rejected,'original_files_unchanged':True,
        'model_calls':0,'network_calls':0,'analysis_sources_unchanged':subprocess.check_output(
            ['git','diff','--name-only','098a56c','--','ForecastAgent/market_repricing/forecast.py',
             'ForecastAgent/market_repricing/official_map.py'],text=True).strip()==''}
    write(output/'regression.json',report)
    return report


def pilot(root, output, keys):
    """Fresh shadow ledger, frozen identities; never reopen production tasks."""
    from ForecastAgent.market_repricing.worker import acquire,provider_budget
    from ForecastAgent.market_repricing.materials import prepare
    from ForecastAgent.market_repricing.evidence_capture import VERSION
    from ForecastAgent.market_repricing import SUPER,ENGINE_SHA
    import os
    os.environ['FORECAST_MODEL']=SUPER
    os.environ['FORECAST_MODEL_FALLBACK_SUPER']='0'
    os.environ['GITHUB_RUN_ID']='local-p0-shadow-20261014'
    catalog=read('D:/metaculus/.tmp/market-context-live-38553757134/summary.json')
    entries=catalog['sources']['market-repricing-v2-worker-state']['state.json']['tasks']
    entries=list(entries.values()) if isinstance(entries,dict) else entries
    chosen={t['id']:t for t in entries if t['id'] in keys}
    if len(chosen)!=len(keys) or not 1<=len(keys)<=3:raise ValueError('Exactly one to three frozen market identities required')
    frozen={k:copy.deepcopy(t['market']) for k,t in chosen.items()}
    pin={'schema':'market-p0-shadow-input-v1','keys':keys,'markets':frozen,'engine':ENGINE_SHA,
         'model':SUPER,'analysis_run':False,'maximum_model_posts':45,'maximum_search_posts':12,
         'new_shadow_budgets_not_production_reset':True}
    if (output/'pilot-pin.json').exists() and read(output/'pilot-pin.json')!=pin:raise ValueError('Pilot pin changed')
    write(output/'pilot-pin.json',pin)
    from ForecastAgent.market_repricing.common import PublicHTTP
    from ForecastAgent.market_repricing.venues import refresh
    preflight_path=output/'market-preflight.json'
    if not preflight_path.exists():
        public=PublicHTTP(output/'public-market-preflight',cap=3)
        metadata=[]
        for key in keys:
            current,receipt=refresh(public,frozen[key])
            metadata.append({'key':key,'platform_status':current['platform_status'],
                'rules_unchanged':current['rules_sha256']==frozen[key]['rules_sha256'],
                'question_unchanged':current['question']==frozen[key]['question'],
                'received_at':receipt['received_at']})
        write(preflight_path,metadata)
    preflight=read(preflight_path)
    if any(r['platform_status']!='open' or not r['rules_unchanged'] or not r['question_unchanged'] for r in preflight):
        raise ValueError('Current public rules/status differ; preserve preflight and do not collect')
    cfg=policy()|{'maximum_worker_model_attempts_per_dispatch':45,'maximum_worker_model_attempts_per_task':20,
                  'maximum_worker_search_attempts_per_dispatch':12}
    ledger=read(output/'state.json') if (output/'state.json').exists() else {'tasks':{},'provider_reservations':[]}
    reports=[]
    for key in keys:
        task=ledger['tasks'].setdefault(key,{'market':frozen[key],'key':key,'status':'shadow_pending',
             'enrolled_at':now(),'acquisition_policy':VERSION})
        folder=output/'tasks'/digest(key);folder.mkdir(parents=True,exist_ok=True)
        try:
            with provider_budget(output,ledger,key,cfg):bundle=acquire(task,folder)
            view,audit=prepare(bundle)
            result={'key':key,'status':'collected','readable_originals':len(view['pages']),
                'full_body_count':audit['external_full_body_count'],'capture_summary':task.get('capture_summary'),
                'body_roles':{u:p['body_diagnostics'].get('body_role') for u,p in view['pages'].items()},
                'capture_gaps':bundle.get('market_capture',{}).get('checklist',{}),
                'original_snapshot_not_replaced':True,'analysis_run':False}
            task['status']='shadow_collected'
        except Exception as exc:
            task['status']='shadow_incomplete'
            result={'key':key,'status':'incomplete','error_type':type(exc).__name__,'detail':str(exc)[:500],
                    'saved_files_preserved':True,'analysis_run':False}
        reports.append(result);write(output/'state.json',ledger);write(folder/'pilot-result.json',result)
    report={'schema':'market-p0-live-shadow-v1','checked_at':now(),'cases':reports,
         'physical_reservations':ledger['provider_reservations'],'production_modified':False,
         'forecasts_submitted':False,'trades':False,'analysis_run':False,'engine_sha':ENGINE_SHA}
    write(output/'pilot-report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--engine',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--root',default='E:/metaculus_data');parser.add_argument('--live',action='store_true')
    args=parser.parse_args();activate(args.engine);output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    test=unittest.defaultTestLoader.loadTestsFromNames([
        'ForecastAgent.market_repricing.tests.test_acquisition_contract',
        'ForecastAgent.market_repricing.tests.test_source_profile',
        'ForecastAgent.market_repricing.tests.test_evidence_capture'])
    outcome=unittest.TextTestRunner(verbosity=1).run(test)
    if not outcome.wasSuccessful():raise SystemExit('Offline gate failed')
    r=regression(Path(args.root),output);print(json.dumps({'regression_cases':r['case_count'],'new_body_rejections':len(r['new_body_rejections'])}),flush=True)
    if args.live:
        r=pilot(Path(args.root),output,['polymarket:5609931','polymarket:5621740','polymarket:5623289'])
        print(json.dumps({'pilot_cases':[{k:v for k,v in t.items() if k in ('key','status','error_type','readable_originals')} for t in r['cases']]}),flush=True)


if __name__=='__main__':main()
