"""Network-free three-arm market-price and eventual-outcome evaluation."""
import argparse
from collections import Counter
import hashlib
import math
from pathlib import Path
import sqlite3

from .common import digest,instant,now,read,write
from .material_revisions import forecast_eligibility

ARMS=('original','mapped','official_map')


def evaluate(report,tracking):
    rows=[];events=[]
    for case in report['cases']:
        observed=tracking.get('tasks',{}).get(case['key'],{})
        for hour,target in case['price_schedule']['targets'].items():
            label=observed.get('prices',{}).get(hour,{})
            row={'key':case['key'],'family':case['family'],'horizon_hours':int(hour),'target_at':target,
                'arms':{},'eligibility':{}}
            for arm in ARMS:
                prediction=case['arms'].get(arm,{})
                forecast={'sealed_at':prediction.get('sealed_at'),'price_schedule':case['price_schedule'],
                    'timely':prediction.get('status')=='completed',
                    'eligible_for_model_metrics':prediction.get('eligible_for_model_metrics',False)}
                eligibility=forecast_eligibility(forecast,hour,label)
                row['eligibility'][arm]=eligibility
                if eligibility=='eligible' and hour in prediction.get('price_means',{}):
                    value=prediction['price_means'][hour]
                    row['arms'][arm]={'prediction':value,'label':label['label'],'error':value-label['label']}
                    row['no_change_error']=case['anchor']['midpoint']-label['label']
            rows.append(row)
        final=observed.get('final',{})
        item={'key':case['key'],'family':case['family'],'status':'waiting_for_resolution','arms':{}}
        if final.get('eligible_for_event_brier') is True:
            label=1 if final['result']=='yes' else 0
            for arm in ARMS:
                prediction=case['arms'].get(arm,{})
                # A settled observation without a verified resolution timestamp
                # cannot establish the prediction preceded the outcome.
                if prediction.get('status')!='completed' or not final.get('resolved_at'):
                    continue
                if instant(prediction['sealed_at'])>=instant(final['resolved_at']):continue
                p=prediction['eventual_probability_yes'];item['arms'][arm]={'probability_yes':p,'label':label,'brier':(p-label)**2}
            if item['arms']:item['status']='scored'
        events.append(item)
    summary={}
    for h in (24,72,168):
        pool=[r for r in rows if r['horizon_hours']==h]
        paired=[r for r in pool if set(ARMS)<=set(r['arms'])]
        metrics={}
        for arm in ('no_change',)+ARMS:
            errors=[r['no_change_error'] if arm=='no_change' else r['arms'][arm]['error'] for r in paired]
            metrics[arm]={'paired_n':len(errors),'mae':sum(map(abs,errors))/len(errors) if errors else None,
                'rmse':math.sqrt(sum(e*e for e in errors)/len(errors)) if errors else None}
        summary[str(h)]={'paired_metrics':metrics,'independent_event_families':len({r['family'] for r in paired}),
            'eligibility_by_arm':{a:dict(Counter(r['eligibility'][a] for r in pool)) for a in ARMS}}
    paired_events=[e for e in events if set(ARMS)<=set(e['arms'])]
    event_metrics={a:{'paired_n':len(paired_events),'brier':sum(e['arms'][a]['brier'] for e in paired_events)/len(paired_events) if paired_events else None} for a in ARMS}
    return {'schema':'official-map-paired-metrics-v1','rows':rows,'summary':summary,'event_rows':events,
        'event_metrics':event_metrics,'price_errors_are_distinct_from_event_brier':True,'trading_pnl_estimated':False}


def refresh(data_root):
    root=Path(data_root);pointer=root/'prediction_markets/official-map-trial-index.json'
    if not pointer.exists():return {'status':'no_official_map_trial','network_requests':0}
    index=read(pointer);report_path=Path(index['report_file']);raw=report_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=index['report_sha256']:raise ValueError('Frozen official-map report changed')
    report=__import__('json').loads(raw)
    for case in report['cases']:
        paths=index['case_files'][case['key']]
        graph=read(Path(paths['graph']))
        if digest(graph['graph'])!=case['reference_graph_sha256']:raise ValueError('Official graph changed')
        for arm,path in paths['states'].items():
            if digest(read(Path(path)))!=case['arms'][arm]['state_sha256']:raise ValueError('Scoring originals or delivered map changed')
    db=sqlite3.connect('file:'+(root/'index.sqlite3').as_posix()+'?mode=ro',uri=True)
    try:
        record=db.execute("SELECT f.sha256,a.run_id,a.artifact_id FROM files f JOIN artifacts a ON f.repo=a.repo AND f.artifact_id=a.artifact_id WHERE f.path='tracking.json' AND a.name='market-repricing-v2-monitor-state' AND a.status IN ('imported','files_imported') ORDER BY CAST(a.run_id AS INTEGER) DESC LIMIT 1").fetchone()
    finally:db.close()
    tracking={'tasks':{}};source={'status':'no_archived_tracking'}
    if record:
        sha,run,artifact=record;raw=(root/'blobs/sha256'/sha[:2]/sha).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Price tracking changed')
        tracking=__import__('json').loads(raw);source={'run_id':run,'artifact_id':artifact,'sha256':sha}
    result=evaluate(report,tracking)
    result.update(generated_at=now(),network_requests=0,provider_calls=0,price_source=source,
        status='scored' if any(r['arms'] for r in result['rows']) else 'waiting_for_labels')
    output=root/'reports/market-official-map-price-metrics.json';write(output,result)
    return {'status':result['status'],'cases':len(report['cases']),'output':str(output),'network_requests':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='E:/metaculus_data')
    print(__import__('json').dumps(refresh(parser.parse_args().root)))
