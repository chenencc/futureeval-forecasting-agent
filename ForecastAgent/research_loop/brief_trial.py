"""Frozen target-first evidence, paired direct and brief-assisted Mercury scores."""
import argparse
from pathlib import Path

from ForecastAgent.acquisition import handoff
from ForecastAgent.analysis.pilot import digest, load, save, WARNING
from ForecastAgent.releases.context_decisions import select_first
from ForecastAgent.research_loop import decision, live_trial, target_pack, forecast_brief
from ForecastAgent.runtime.task_lock import task_lock

PROTOCOL='target-first-brief-paired-v1'


def receipts(root):
    return sorted(Path(root).glob('cases/**/http/*.json'))


def coverage(first,second):
    added=removed=0
    for before,after,label in ((first,second,'removed'),(second,first,'added')):
        count=sum(sum(b-a for a,b in handoff.uncovered(e['start'],e['end'],
            [(v['start'],v['end']) for v in after['evidence'] if handoff.space(v)==handoff.space(e)]))
            for e in before['evidence'])
        if label=='removed': removed=count
        else: added=count
    return {'new_coordinate_chars':added,'old_visible_coordinate_chars_removed':removed,
        'same_source_snapshots':True,'same_visible_text_as_old_selector':added==removed==0,
        'interpretation':'Packing comparison changes visible spans. Forecast arms share the exact new selected originals.'}


def run(parents,root,*,execute=False,max_http=6,brief_protocol='literal',context_protocol='target-v1'):
    if not 1<=len(parents)<=5 or type(max_http) is not int or max_http<0:
        raise ValueError('Use 1-5 frozen cases and a nonnegative physical request cap')
    if brief_protocol not in ('literal','references'):
        raise ValueError('Unknown brief interface')
    if context_protocol not in ('target-v1','target-v2'):
        raise ValueError('Unknown saved-text selector')
    if context_protocol=='target-v2':
        from ForecastAgent.research_loop import target_pack_v2 as context_engine
    else:context_engine=target_pack
    if brief_protocol=='references':
        from ForecastAgent.research_loop import forecast_brief_refs as brief_engine
    else:brief_engine=forecast_brief
    root=Path(root); root.mkdir(parents=True,exist_ok=True); frozen=[]
    for path in parents:
        path=Path(path).resolve(); original=load(path)
        frozen.append({'id':str(original['request']['id']),'path':str(path),'sha256':live_trial.sha(path)})
    if len({e['id'] for e in frozen})!=len(frozen):
        raise ValueError('Duplicate question ID')
    identity={'schema':PROTOCOL,'inputs':frozen,'implementation':decision.implementation_hashes(),
        'max_http':max_http,'per_stage_http_cap':1,'model':live_trial.SUPER,
        'comparison':'Same selected original evidence and same Mercury question registry; optional brief differs.',
        'prior_experiment_budgets_unchanged':True,'searches':0,'fetches':0,'submitted':False}
    if brief_protocol=='references':identity['brief_protocol']=brief_engine.PROTOCOL
    if context_protocol=='target-v2':identity['context_protocol']=context_engine.PROTOCOL
    with task_lock(root):
        if (root/'identity.json').exists() and load(root/'identity.json')!=identity:
            raise ValueError('Frozen experiment source, code or lifetime budget changed')
        save(root/'identity.json',identity); rows=[]
        for position,entry in enumerate(frozen):
            if live_trial.sha(entry['path'])!=entry['sha256']:
                raise ValueError('Frozen parent file changed')
            bundle=load(entry['path']); heads,spec=forecast_brief.registry(bundle['request'])
            common,audit=context_engine.pack(bundle,heads)
            old,_=select_first(bundle,heads)
            folder=root/'cases'/entry['id']
            save(folder/'common-state.json',common); save(folder/'questions.json',heads)
            save(folder/'spec.json',spec); save(folder/'old-selector-state.json',old)
            save(folder/'packing-audit.json',{'new':audit,'comparison':coverage(old,common)})
            row={'id':entry['id'],'question':bundle['request']['question'],
                 'type':bundle['request']['question_type'],'status':'prepared',
                 'original_state_sha256':digest(common),'arms':{},'packing':coverage(old,common)}
            if execute:
                order=['direct','brief'] if position%2==0 else ['brief','direct']
                row['arm_order']=order
                for arm in order:
                    try:
                        accepted=None
                        if arm=='brief':
                            try:
                                cached=(folder/'brief/message.json').exists()
                                score_cached=(folder/arm/'decision/response.json').exists()
                                needed=(0 if cached else 1)+(0 if score_cached else 1)
                                if len(receipts(root))+needed>max_http:
                                    raise RuntimeError('Reserve final attempt for original-evidence scoring')
                                accepted=brief_engine.brief_stage(common,folder/'brief')
                                row['brief_status']='completed'
                            except Exception as exc:
                                row['brief_status']='unavailable'
                                row['brief_error']=type(exc).__name__+': '+str(exc)
                        if not (folder/arm/'decision/response.json').exists() and len(receipts(root))>=max_http:
                            raise RuntimeError('Preserved experiment HTTP cap exhausted before score')
                        result=brief_engine.score(bundle['request'],common,heads,spec,folder/arm,accepted)
                        row['arms'][arm]={'status':'completed','summary':result['summary'],
                            'brief_delivered':result['forecast_brief_delivered'],
                            'original_state_sha256':result['original_state_sha256']}
                    except Exception as exc:
                        row['arms'][arm]={'status':'failed','error':type(exc).__name__+': '+str(exc)}
                row['forecasts_completed']=all(a['status']=='completed' for a in row['arms'].values())
                row['paired_completed']=row['forecasts_completed'] and row['arms']['brief']['brief_delivered']
                row['status']='paired_completed' if row['paired_completed'] else 'scored_without_brief' if row['forecasts_completed'] else 'incomplete'
                if len({a.get('original_state_sha256') for a in row['arms'].values() if a['status']=='completed'})>1:
                    raise ValueError('Forecast arms did not share exact original evidence')
            if live_trial.sha(entry['path'])!=entry['sha256']:
                raise ValueError('Parent snapshot mutated during experiment')
            row['source_preserved']=True
            save(folder/'result.json',row); rows.append(row)
            usage=live_trial.usage(receipts(root))
            if usage['totals']['http_attempts']>max_http:
                raise ValueError('Physical HTTP cap exceeded')
            report={'schema':PROTOCOL,'execute':execute,'cases':rows,'requested_cases':len(frozen),
                'completed':sum(r.get('forecasts_completed',False) for r in rows),
                'paired_completed':sum(r.get('paired_completed',False) for r in rows), 'usage':usage,
                'authorized_http_cap':max_http,'new_http':len(receipts(root)),
                'prior_experiment_budgets_unchanged':True,'searches':0,'fetches':0,'submitted':False,
                'accuracy':None,'brier':None,'evaluation_warning':WARNING,
                'conditional_scoring_tested':False}
            if brief_protocol=='references':report['brief_protocol']=brief_engine.PROTOCOL
            if context_protocol=='target-v2':report['context_protocol']=context_engine.PROTOCOL
            save(root/'report.json',report)
            print({'id':entry['id'],'status':row['status'],'http':len(receipts(root))},flush=True)
        return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parents',type=Path,required=True);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--max-http',type=int,default=6);p.add_argument('--execute',action='store_true')
    p.add_argument('--brief-protocol',choices=['literal','references'],default='literal')
    p.add_argument('--context-protocol',choices=['target-v1','target-v2'],default='target-v1')
    a=p.parse_args();run(load(a.parents),a.root,execute=a.execute,max_http=a.max_http,
        brief_protocol=a.brief_protocol,context_protocol=a.context_protocol)


if __name__=='__main__':main()
