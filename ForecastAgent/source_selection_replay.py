"""No-network request sizing and body-guard audit over completed experiment artifacts."""
import argparse
from pathlib import Path
from ForecastAgent.analysis.pilot import load,save,digest
from ForecastAgent.analysis.mercury_evidence_chain import request_bytes
from ForecastAgent.providers.source_selection import batches,PROTOCOL
from ForecastAgent.providers.source_selection_guards import rule_sources,screen_bundle
from ForecastAgent.source_selection_trial import selection_question


def review(inputs,output):
    rows=[]
    for task in sorted(Path(inputs).glob('*/tasks/*')):
        if not (task/'baseline-original.json').exists():continue
        baseline=load(task/'baseline-original.json'); before=digest(baseline)
        question=selection_question(baseline); candidates=load(task/'candidates.json')
        packets=list(batches(question,candidates)); rules=rule_sources(question,candidates)
        row={'id':task.name,'candidate_count':len(candidates),'planned_http_batches':len(packets),
             'planned_request_bytes':[request_bytes(state,registry) for _,state,registry in packets],
             'priority_heads':sum(len(registry) for _,_,registry in packets),
             'reserved_rule_urls':[c['url'] for c in candidates if c['candidate_id'] in rules],
             'previous_actual_http_batches':len(list(task.glob('selection/batch-*/http/*.json'))),'arms':{}}
        for arm in ('old_selection_recaptured','mercury_selection'):
            original=load(task/arm/'collected.json'); identity=digest(original)
            screened=screen_bundle(original)
            row['arms'][arm]={'original_pages':len(original['pages']),'eligible_pages':len(screened['pages']),
                             'exclusions':screened['selection_acquisition_gaps'],
                             'input_unchanged':digest(load(task/arm/'collected.json'))==identity,
                             'raw_body_hashes':{u:digest(p.get('content','')) for u,p in screened.get('selection_excluded_pages',{}).items()}}
        assert digest(load(task/'baseline-original.json'))==before
        rows.append(row)
    if not rows:raise ValueError('No completed source-selection artifacts found')
    result={'schema':'source-selection-v2-offline-review','protocol':PROTOCOL,'rows':rows,
        'previous_actual_http_batches':sum(r['previous_actual_http_batches'] for r in rows),
        'planned_http_batches':sum(r['planned_http_batches'] for r in rows),
        'priority_heads':sum(r['priority_heads'] for r in rows),'provider_calls':0,'search_calls':0,'fetch_calls':0,
        'scope':'Request sizing and deterministic guard replay only; new Mercury ranking quality and actual token consumption have not been measured.'}
    save(Path(output),result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--inputs',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();report=review(args.inputs,args.output)
    print({k:v for k,v in report.items() if k!='rows'})
