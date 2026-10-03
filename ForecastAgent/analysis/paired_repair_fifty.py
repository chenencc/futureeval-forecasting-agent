"""Compare preserved before/after repair forecasts on the identical scored cohort."""
import argparse
import csv
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save, digest, WARNING


def aggregate(rows,prefix):
    if not rows:return {'n':0}
    return {'n':len(rows),**{k:sum(r[prefix+'_'+k] for r in rows)/len(rows)
        for k in ('brier','log_loss','correct_at_half')}}


def compare(old_results,new_results,old_metrics,new_metrics,output):
    old_results,new_results,output=map(Path,(old_results,new_results,output))
    old,new=load(old_metrics),load(new_metrics)
    if old['labels_sha256']!=new['labels_sha256']:raise ValueError('Resolution labels changed')
    old_manifests=[load(p) for p in old_results.glob('repaired-fifty-analysis-*/manifest.json')]
    new_manifests=[load(p) for p in new_results.glob('repaired-fifty-analysis-*/manifest.json')]
    if len(old_manifests)!=10 or len(new_manifests)!=10:raise ValueError('All analysis manifests required')
    for key in ('chain_sha256','chain_protocol','model','physical_http_cap_per_task','all_ids'):
        if len({digest(m[key]) for m in old_manifests+new_manifests})!=1:
            raise ValueError('Analysis policy or cohort differs: '+key)
    before={r['id']:r for r in old['results']};after={r['id']:r for r in new['results']}
    intended=set(old_manifests[0]['all_ids'])-{'43900','44799'}
    ids=sorted(set(before)&set(after)&intended,key=int)
    rows=[]
    for ident in ids:
        a,b=before[ident],after[ident]
        if a['resolution']!=b['resolution']:raise ValueError('Outcome changed')
        old_bundle=load(next(old_results.glob(f'repaired-fifty-analysis-*/tasks/{ident}/analysis-input.json')))
        new_bundle=load(next(new_results.glob(f'repaired-fifty-analysis-*/tasks/{ident}/analysis-input.json')))
        if old_bundle['request']!=new_bundle['request']:raise ValueError('Question changed')
        old_bodies={u:p.get('content','') for u,p in old_bundle.get('pages',{}).items()}
        new_bodies={u:p.get('content','') for u,p in new_bundle.get('pages',{}).items()}
        added=[u for u in new_bodies if u not in old_bodies]
        unchanged=old_bodies==new_bodies
        row={'id':ident,'resolution':a['resolution'],'before_probability':a['clipped_probability_yes'],
             'after_probability':b['clipped_probability_yes'],'added_pages':added,
             'saved_bodies_identical':unchanged,'added_body_group':bool(added),
             'acquisition_gap_metadata_identical':old_bundle.get('gaps',[])==new_bundle.get('gaps',[]),
             'probability_change':b['clipped_probability_yes']-a['clipped_probability_yes']}
        for k in ('brier','log_loss','correct_at_half'):
            row['before_'+k]=a[k];row['after_'+k]=b[k]
        row['brier_change']=b['brier']-a['brier']
        row['log_loss_change']=b['log_loss']-a['log_loss']
        row['transition']='wrong_to_correct' if not a['correct_at_half'] and b['correct_at_half'] else 'correct_to_wrong' if a['correct_at_half'] and not b['correct_at_half'] else 'unchanged_classification'
        rows.append(row)
    def group(selected):
        return {'before':aggregate(selected,'before'),'after':aggregate(selected,'after'),
            'mean_brier_change':sum(r['brier_change'] for r in selected)/len(selected) if selected else None,
            'wrong_to_correct':sum(r['transition']=='wrong_to_correct' for r in selected),
            'correct_to_wrong':sum(r['transition']=='correct_to_wrong' for r in selected),
            'mean_absolute_probability_change':sum(abs(r['probability_change']) for r in selected)/len(selected) if selected else None}
    report={'schema':'paired-free-repair-fifty-v1','intended_questions':48,'paired_questions':len(rows),
        'excluded_ids':['43900','44799'],'unpaired_ids':sorted(intended-set(ids),key=int),
        'overall':group(rows),'added_body_group':group([r for r in rows if r['added_body_group']]),
        'no_added_body_group':group([r for r in rows if not r['added_body_group']]),
        'by_outcome':{str(y):group([r for r in rows if r['resolution']==y]) for y in (0,1)},
        'before_http_attempts':old['http_attempts'],'before_known_tokens':old['known_tokens'],
        'after_http_attempts':new['http_attempts'],'after_known_tokens':new['known_tokens'],
        'before_metrics_sha256':digest(old),'after_metrics_sha256':digest(new),'rows':rows,
        'evaluation_warning':WARNING,
        'causal_limit':'Before is a fresh replay; after is reused. Probability changes can reflect evidence selection, gap metadata or model variability. No repeated-run control or prospective evaluation.'}
    save(output/'paired-metrics.json',report)
    with (output/'paired-predictions.csv').open('w',newline='',encoding='utf-8') as file:
        fields=['id','resolution','before_probability','after_probability','before_brier','after_brier','brier_change','before_correct_at_half','after_correct_at_half','transition','added_body_group','saved_bodies_identical','acquisition_gap_metadata_identical']
        writer=csv.DictWriter(file,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    print(__import__('json').dumps({k:report[k] for k in ('paired_questions','overall','added_body_group','no_added_body_group')},indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('old-results','new-results','old-metrics','new-metrics','output'):p.add_argument('--'+name,required=True)
    a=p.parse_args();compare(a.old_results,a.new_results,a.old_metrics,a.new_metrics,a.output)
