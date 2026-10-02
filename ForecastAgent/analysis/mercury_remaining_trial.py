"""Mercury-only completion of the frozen twenty-question direct evidence trial."""
import argparse
import hashlib
from pathlib import Path

from ForecastAgent.analysis import super_direct_trial, referenced
from ForecastAgent.analysis.inputs import resolve_bundle
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.providers import decisions


def run(inputs, supplements, output, batch):
    root = Path(__file__).parent
    cohort = load(root/'mercury_remaining13_cohort.json')
    ids = cohort['batches'][batch-1]
    excluded = load(root/'error_seven_time_metadata.json')
    if len({i for b in cohort['batches'] for i in b}) != 13 or any(i in excluded for i in ids):
        raise ValueError('Remaining thirteen identity invalid')
    metadata = load(root/'three_route_time_metadata.json')
    output = Path(output)
    identity = {'cohort':cohort,'batch':batch,'ids':ids,'model':decisions.MODEL,
                'metadata_sha256':digest(metadata),
                'selector_sha256':hashlib.sha256(Path(super_direct_trial.__file__).read_bytes()).hexdigest(),
                'http_cap_per_question':1,'no_super_calls':True,'no_jev_calls':True}
    if (output/'selection.json').exists() and load(output/'selection.json') != identity:
        raise ValueError('Frozen Mercury trial changed')
    save(output/'selection.json',identity)
    rows=[]
    for ident in ids:
        folder=output/'tasks'/ident
        try:
            bundle=resolve_bundle(load(Path(inputs)/'tasks'/ident/'bundle.json'),ident,supplements)
            packet=referenced.evidence_packet(bundle);packet['question'].update(metadata[ident])
            state,audit=super_direct_trial.compact(packet)
            if (folder/'state.json').exists() and load(folder/'state.json') != state:
                raise ValueError('Frozen direct evidence state changed')
            save(folder/'state.json',state);save(folder/'input-audit.json',audit)
            response=super_direct_trial.direct(state,decisions.MODEL,folder)
            row={'id':ident,'probability_yes':response['answers']['event_yes']['noul'],
                 'state_sha256':digest(state),'status':'completed'}
        except Exception as exc:
            row={'id':ident,'probability_yes':None,'status':'failed','error':str(exc)}
        save(folder/'result.json',row);rows.append(row)
    # Outcomes never enter state; evaluation happens only after provider calls.
    labels=load(root/'three_route_labels.json')
    for row in rows:row['resolution']=labels[row['id']]
    save(output/'report.json',{'batch':batch,'rows':rows,'no_super_calls':True,
                              'no_jev_calls':True,'no_retrieval_calls':True,'no_forecasts_submitted':True})
    if any(r['status']=='failed' for r in rows):raise RuntimeError('Preserved Mercury-only failures')


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ['inputs','supplements','output']:p.add_argument('--'+name,required=True)
    p.add_argument('--batch',type=int,choices=[1,2,3],required=True)
    a=p.parse_args();run(a.inputs,a.supplements,a.output,a.batch)
