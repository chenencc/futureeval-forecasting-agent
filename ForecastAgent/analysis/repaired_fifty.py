"""Replay fifty repaired snapshots with the retained Mercury evidence chain."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from ForecastAgent.analysis import mercury_evidence_chain as chain
from ForecastAgent.analysis.ensemble import metrics
from ForecastAgent.analysis.pilot import WARNING, digest, load, save

PROTOCOL='repaired-fifty-mercury-v1'


def run(inputs,output,batch,*,dry_run=False,variant='after'):
    inputs,output=Path(inputs),Path(output)
    if variant not in ('before','after'):raise ValueError('Known evidence variant required')
    pattern='handoffs/*/analysis-input.json' if variant=='before' else 'recollection-fifty-free-repair-*/tasks/*/analysis-input.json'
    paths=sorted(inputs.glob(pattern),key=lambda p:int(p.parent.name))
    if len(paths)!=50 or len({p.parent.name for p in paths})!=50:
        raise ValueError('Exactly fifty unique repaired inputs required')
    if batch not in range(10):raise ValueError('Ten fixed batches of five required')
    selected=paths[batch*5:(batch+1)*5]
    identity={'protocol':PROTOCOL,'chain_protocol':chain.PROTOCOL,'model':chain.decisions.MODEL,
        'batch':batch,'all_ids':[p.parent.name for p in paths],
        'inputs':{p.parent.name:digest(load(p)) for p in selected},'dry_run':dry_run,
        'physical_http_cap_per_task':2,'outcome_labels_loaded':False,
        'chain_sha256':hashlib.sha256(Path(chain.__file__).read_bytes()).hexdigest()}
    if variant=='before':
        identity.update(evidence_variant='before_free_extension',excluded_ids=['43900','44799'])
    if (output/'manifest.json').exists() and load(output/'manifest.json')!=identity:
        raise ValueError('Frozen analysis input or version changed')
    save(output/'manifest.json',identity)
    rows=[]
    for path in selected:
        ident=path.parent.name;bundle=load(path);task=output/'tasks'/ident
        save(task/'analysis-input.json',bundle)
        row={'id':ident,'status':'failed','bundle_sha256':digest(bundle),
             'saved_pages':len(bundle.get('pages',{})),'acquisition_gaps':bundle.get('gaps',[])}
        if variant=='before' and ident in identity['excluded_ids']:
            row.update(status='skipped_by_user',error='Excluded empty-body case; no model request')
            save(task/'outcome.json',row);rows.append(row)
            save(output/'report.json',{'protocol':PROTOCOL,'batch':batch,'rows':rows,
                'outcome_labels_loaded':False,'new_retrieval_calls':0,'forecast_submissions':0})
            continue
        try:
            row.update(chain.run_task(bundle,task/'mercury',dry_run=dry_run))
        except Exception as exc:
            row['error']=str(exc)
            # Retain an actually validated first forecast when only rereading fails.
            # Its request and response remain unchanged and failure stays explicit.
            if isinstance(exc,RuntimeError) and (task/'mercury/first/response.json').exists() and not (task/'mercury/second/response.json').exists():
                registry=chain.questions()
                response=chain.decisions.validate(load(task/'mercury/first/response.json'),registry)
                p=response['answers']['event_yes']['noul']
                row.update(status='completed_first_retained',probability_yes=p,
                           clipped_probability_yes=min(.98,max(.02,p)),second_error=str(exc))
        save(task/'outcome.json',row)
        rows.append(row)
        save(output/'report.json',{'protocol':PROTOCOL,'batch':batch,'rows':rows,
            'evaluation_warning':WARNING,'outcome_labels_loaded':False,
            'new_retrieval_calls':0,'forecast_submissions':0})
        print(json.dumps({'id':ident,'status':row['status']}),flush=True)
    return rows


def evaluate(results,labels,output):
    """Freeze and check provider outputs before opening the separate label file."""
    results,labels,output=Path(results),Path(labels),Path(output)
    paths=sorted(results.glob('repaired-fifty-analysis-*/tasks/*/outcome.json'))
    if len(paths)!=50 or len({p.parent.name for p in paths})!=50:
        raise ValueError('All fifty terminal analysis records required')
    frozen=[];usage=[]
    for path in paths:
        row=load(path);folder=path.parent/'mercury'
        if row['bundle_sha256']!=digest(load(path.parent/'analysis-input.json')):
            raise ValueError('Analysis bundle changed')
        if 'probability_yes' in row:
            stage='second' if row['status']=='completed' and row.get('second_call_required') else 'first'
            response=chain.decisions.validate(load(folder/stage/'response.json'),chain.questions())
            if row['probability_yes']!=response['answers']['event_yes']['noul']:
                raise ValueError('Prediction changed after inference')
            if row['clipped_probability_yes']!=min(.98,max(.02,row['probability_yes'])):
                raise ValueError('Probability clip changed')
        for journal in sorted(folder.glob('*/http/*.json')):
            record=load(journal);data=record.get('response',{}).get('usage',{}) or {}
            tokens=data.get('total_tokens')
            if tokens is None and isinstance(data.get('input_tokens'),int) and isinstance(data.get('output_tokens'),int):
                tokens=data['input_tokens']+data['output_tokens']
            usage.append({'id':row['id'],'status':record.get('status'),'http_status':record.get('http_status'),
                          'model':record['request']['model'],'tokens':tokens,'cost_usd':data.get('cost')})
        frozen.append(row)
    frozen_digest=digest(frozen)
    label_rows=load(labels)
    outcomes={str(r['id']):r for r in label_rows['labels']}
    scored=[];failures=[]
    for row in frozen:
        if 'probability_yes' not in row:
            failures.append(row);continue
        y=outcomes[row['id']]['resolved_to'];p=row['clipped_probability_yes']
        scored.append({**row,'resolution':y,**metrics(p,y)})
    def aggregate(rows):
        return {'n':len(rows),**({k:sum(r[k] for r in rows)/len(rows) for k in ('brier','log_loss','correct_at_half')} if rows else {})}
    report={'protocol':PROTOCOL,'evaluation_warning':WARNING,'requested':50,'scored':len(scored),
        'coverage':len(scored)/50,'metrics':aggregate(scored),
        'by_outcome':{str(y):aggregate([r for r in scored if r['resolution']==y]) for y in (0,1)},
        'by_source_gap':{str(g):aggregate([r for r in scored if bool(r['acquisition_gaps'])==g]) for g in (False,True)},
        'majority_class_accuracy_on_scored':max(Counter(r['resolution'] for r in scored).values(),default=0)/len(scored) if scored else None,
        'neutral_half_brier':.25,'state_distribution':dict(Counter(r['status'] for r in frozen)),
        'http_attempts':len(usage),'known_tokens':sum(r['tokens'] for r in usage if isinstance(r['tokens'],int)),
        'attempts_unknown_usage':sum(r['tokens'] is None for r in usage),
        'known_reported_cost_usd':sum(r['cost_usd'] for r in usage if isinstance(r['cost_usd'],(int,float))),
        'conditional_reread_tasks':sum(bool(r.get('second_call_required')) for r in frozen),
        'frozen_results_sha256':frozen_digest,'label_provenance':label_rows['provenance'],
        'labels_sha256':digest(label_rows),'results':scored,'failures':failures,'usage':usage,
        'new_retrieval_calls':0,'forecast_submissions':0}
    save(output/'metrics.json',report)
    import csv
    with (output/'predictions.csv').open('w',newline='',encoding='utf-8') as file:
        columns=['id','status','saved_pages','probability_yes','clipped_probability_yes','resolution','brier','log_loss','correct_at_half']
        writer=csv.DictWriter(file,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(scored)
    text=json.dumps({k:report[k] for k in ('requested','scored','coverage','metrics','by_outcome','http_attempts','known_tokens')},indent=2)
    print(text)
    import os
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as file:
            file.write('# Repaired fifty Mercury analysis\n\nRetrospective evidence replay; temporal leakage possible.\n\n```json\n'+text+'\n```\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['run','evaluate'])
    p.add_argument('--inputs',required=True);p.add_argument('--output',required=True)
    p.add_argument('--batch',type=int);p.add_argument('--labels');p.add_argument('--dry-run',action='store_true')
    p.add_argument('--variant',choices=['before','after'],default='after')
    a=p.parse_args()
    if a.action=='run':run(a.inputs,a.output,a.batch,dry_run=a.dry_run,variant=a.variant)
    else:evaluate(a.inputs,a.labels,a.output)
