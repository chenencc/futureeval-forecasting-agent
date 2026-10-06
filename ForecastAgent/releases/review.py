"""Audit saved release validation artifacts; do not call any remote provider."""
import argparse
from pathlib import Path
from ForecastAgent.acquisition import pipeline
from ForecastAgent.acquisition.handoff_replay import audit_spans
from ForecastAgent.competition.queue import load, save
from ForecastAgent.releases import v1_0_2 as release


def review(root):
    root=Path(root);cases=[]
    for kind in ('binary','multiple_choice','numeric','discrete','date'):
        folder=root/('release-1.0.2-provider-'+kind)
        if not folder.exists():folder=root/kind
        result_path=folder/'result.json';item={'kind':kind,'status':'missing','real_platform_posts':0}
        if not result_path.exists():
            if (folder/'failure.json').exists():item.update(load(folder/'failure.json'))
            cases.append(item);continue
        release.verify_candidate(folder);item.update(load(result_path));source=load(folder/'analysis-input.json')
        item['candidate_sha256']=release.file_hash(folder/'candidate.json')
        item['source_sha256']=release.file_hash(folder/'analysis-input.json')
        acq=folder/'retrieval/release-1.0.2';native=acq/'collection'
        if (native/'bundle.json').exists():
            b=load(native/'bundle.json')
            item['resources']=pipeline.resource_report(b,native,acq/'supplement')
            r=item['resources']
            assert r['tavily_basic_attempts']<=3 and r['exa_attempts']<=1 and r['initial_source_http_attempts']<=8
            item['original_collection_result']=b['result']
            if (acq/'raw-handoff.json').exists():
                assert release.native_manifest(native)==load(acq/'raw-handoff.json')['native_files_sha256']
                item['raw_handoff_native_bytes_unchanged']=True
        calls=[]
        for path in sorted((folder/'analysis-core-1.0.1').rglob('http/*.json')):
            record=load(path);usage=(record.get('response') or {}).get('usage')
            tokens=usage.get('total_tokens') if isinstance(usage,dict) else None
            if tokens is None and isinstance(usage,dict) and all(type(usage.get(k)) is int for k in ('input_tokens','output_tokens')):
                tokens=usage['input_tokens']+usage['output_tokens']
            calls.append({'path':path.relative_to(folder).as_posix(),'status':record.get('status'),
                          'model':record.get('request',{}).get('model'),
                          'total_tokens':tokens})
        item['decision_transport_records']=calls
        item['known_decision_tokens']=sum(r['total_tokens'] for r in calls if isinstance(r['total_tokens'],int))
        item['unknown_decision_usage_attempts']=sum(not isinstance(r['total_tokens'],int) for r in calls)
        for path in sorted((folder/'analysis-core-1.0.1/mercury-v1.0.1').glob('*-state.json')):
            errors=audit_spans(source,load(path))
            if errors:raise ValueError('Analysis spans differ from saved material: '+str(errors))
        item['original_span_integrity_verified']=True;cases.append(item)
    offline=root/'release-1.0.2-offline/stress-report.json'
    report={'schema':'release-1.0.2-validation-review-v1','cases':cases,
            'real_provider_scored_cases':sum(c['status']=='scored' for c in cases),
            'all_five_provider_cases_scored':all(c['status']=='scored' for c in cases),
            'real_platform_posts':0,'temporal_scope':'Current web on resolved questions; engineering reliability only'}
    if offline.exists():report['offline_stress']=load(offline)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();result=review(args.root);save(args.output,result)
    print({k:v for k,v in result.items() if k!='cases'})
