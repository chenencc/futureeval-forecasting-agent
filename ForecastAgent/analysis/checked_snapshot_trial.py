"""Paired checked-handoff replay with frozen prior repairs and new Mercury calls only."""
import argparse
import copy
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.analysis.inputs import resolve_bundle
from ForecastAgent.analysis import mercury_evidence_chain as binary
from ForecastAgent.analysis import mercury_nonbinary_trial as nonbinary
from ForecastAgent.evidence.collection_handoff import prepare, LEDGERS

BINARY_IDS = ['42491','40695','44547','43822','43461','42489','36871','40852','40849','41206',
              '41480','41489','42485','42630','43491','42490','43844','40417','41485','43987']


def run(cohort, batch, raw, supplements, baseline, output, dry_run=False, strategy='checked'):
    if cohort not in ('binary','nonbinary') or batch not in range(1,5):
        raise ValueError('Frozen cohort and batch required')
    raw, supplements, baseline, output = map(Path,(raw,supplements,baseline,output))
    if strategy not in ('checked','p0','conditions'):raise ValueError('Unknown analysis strategy')
    if cohort == 'binary':
        selected=[{'id':ident} for ident in BINARY_IDS[(batch-1)*5:batch*5]]
        prior={row['id']:row for row in load(baseline/'report.json')['rows']}
    else:
        selected=load(nonbinary.INPUT)[(batch-1)*5:batch*5]
        prior={str(row['post_id']):row for row in load(raw/'report.json')['rows']}
    frozen=[]
    for row in selected:
        ident=str(row.get('id',row.get('post_id')))
        bundle=load(raw/'tasks'/ident/('bundle.json' if cohort=='binary' else 'acquisition/bundle.json'))
        baseline_row=prior[ident]
        if baseline_row['status']!='completed':raise ValueError('Incomplete baseline: '+ident)
        repair_root=supplements if cohort=='binary' else raw/'tasks'/ident/'supplement'
        saved=resolve_bundle(bundle,ident,repair_root)
        frozen.append((ident,row,bundle,saved,baseline_row))
    identity={'protocol':'checked-snapshot-paired-v1','cohort':cohort,'batch':batch,'dry_run':dry_run,
              'tasks':[{'id':i,'raw_sha256':digest(b),'prior_repair_overlay_sha256':digest(s),
                        'baseline_sha256':digest(p)} for i,r,b,s,p in frozen],
              'new_searches':0,'new_network_repairs':0,'mercury_http_cap_per_task':2,
              'model':'inception/mercury-decide:free','outcome_labels_loaded':False}
    if strategy in ('p0','conditions'):identity['strategy']=strategy
    if (output/'selection.json').exists() and load(output/'selection.json')!=identity:
        raise ValueError('Frozen paired experiment changed')
    save(output/'selection.json',identity)
    results=[]
    for ident,row,bundle,saved,prior_row in frozen:
        folder=output/'tasks'/ident
        try:
            def replay_repair(view,repair_folder,task_id):
                overlay=copy.deepcopy(saved)
                overlay['program_inspection']=copy.deepcopy(view['program_inspection'])
                return overlay
            if strategy in ('p0','conditions'):
                # Replay the original acquisition and repair without the experimental screening layer.
                view=copy.deepcopy(saved)
                save(folder/'handoff/raw-bundle.json',bundle)
                save(folder/'handoff/report.json',{'raw_parent_bundle_sha256':digest(bundle),
                     'analysis_input_sha256':digest(view),'raw_page_count':len(bundle['pages']),
                     'analysis_page_count':len(view['pages']),'excluded_page_count':0,'duplicate_count':0,
                     'gaps':view.get('gaps',[]),'collection_ledgers_unchanged':True,'screening_enabled':False})
            else:view=prepare(bundle,folder/'handoff',network=False,repair=replay_repair)
            assert all(digest(view.get(k,[]))==digest(bundle.get(k,[])) for k in LEDGERS)
            save(folder/'baseline-result.json',prior_row)
            save(folder/'analysis-input.json',view)
            if strategy in ('p0','conditions'):
                if strategy=='conditions':
                    from ForecastAgent.analysis.condition_chain import run_task
                else:
                    from ForecastAgent.analysis.p0 import run_task
                metadata=load(Path(binary.__file__).with_name('three_route_time_metadata.json'))[ident] if cohort=='binary' else None
                result=run_task(view,folder/'analysis',row=row if cohort=='nonbinary' else None,metadata=metadata,dry_run=dry_run)
            elif dry_run:
                if cohort=='binary':binary.run_task(view,folder/'analysis',load(Path(binary.__file__).with_name('three_route_time_metadata.json'))[ident],True)
                else:save(folder/'analysis/packet.json',nonbinary.chain.full_packet(view))
                result={'status':'prepared'}
            elif cohort=='binary':
                result=binary.run_task(view,folder/'analysis',load(Path(binary.__file__).with_name('three_route_time_metadata.json'))[ident])
            else:result=nonbinary.analyze(row,view,folder/'analysis')
            result={**result,'id':ident,'cohort':cohort,'baseline':prior_row,
                    'inspection':load(folder/'handoff/report.json'),'new_repair_requests':0,'new_search_requests':0}
        except Exception as exc:
            result={'id':ident,'cohort':cohort,'status':'failed','error':str(exc)}
        results.append(result)
        save(output/'report.json',{'rows':results,'protocol':identity['protocol'],
              'forecast_submissions':0,'outcome_labels_loaded':False,
              'comparison_warning':'Retrospective paired pipeline comparison; prior repairs replayed; no historical cutoff enforcement.'})
        print({'id':ident,'status':result['status']},flush=True)
    if any(row['status']=='failed' for row in results):raise RuntimeError('Failures preserved; resume the exact journals without reset')


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('cohort','raw','supplements','baseline','output'):p.add_argument('--'+name,required=True)
    p.add_argument('--batch',type=int,required=True);p.add_argument('--dry-run',action='store_true')
    p.add_argument('--strategy',choices=['checked','p0','conditions'],default='checked')
    a=p.parse_args();run(a.cohort,a.batch,a.raw,a.supplements,a.baseline,a.output,a.dry_run,a.strategy)
