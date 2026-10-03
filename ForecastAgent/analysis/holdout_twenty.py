"""Fresh old-versus-decomposed binary comparison; labels are never read here."""
import argparse
import copy
from pathlib import Path
from ForecastAgent.analysis import mercury_evidence_chain as old, matched_conditions as new
from ForecastAgent.analysis.inputs import resolve_bundle
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.evidence.collection_handoff import LEDGERS

MANIFEST=Path(__file__).resolve().parents[1]/'fixtures/fresh_binary_holdout20.json'
INPUTS=Path(__file__).resolve().parents[1]/'fixtures/raw_collection_2026_100.json'


def run(batch,raw,supplements,output,dry_run=False):
    if batch not in range(1,5):raise ValueError('Frozen batch required')
    raw,supplements,output=map(Path,(raw,supplements,output))
    manifest=load(MANIFEST);ids=manifest['ids'][(batch-1)*5:batch*5]
    assert len(manifest['ids'])==20 and not set(manifest['ids'])&set(manifest['excluded_analyzed_ids'])
    inputs={str(r['id']):r for r in load(INPUTS)};frozen=[]
    for ident in ids:
        bundle=load(raw/'tasks'/ident/'bundle.json');view=resolve_bundle(bundle,ident,supplements)
        assert all(digest(bundle.get(k,[]))==digest(view.get(k,[])) for k in LEDGERS)
        frozen.append((ident,bundle,view))
    identity={'protocol':'fresh-binary-holdout20-v1','manifest_sha256':digest(manifest),'batch':batch,
              'tasks':[{'id':i,'raw_sha256':digest(b),'overlay_sha256':digest(v)} for i,b,v in frozen],
              'dry_run':dry_run,'max_http_per_task':4,'outcome_labels_loaded':False,'new_collection_requests':0}
    if (output/'selection.json').exists() and load(output/'selection.json')!=identity:
        raise ValueError('Frozen holdout batch changed')
    save(output/'selection.json',identity);rows=[]
    for ident,bundle,view in frozen:
        task=output/'tasks'/ident;save(task/'raw-bundle.json',bundle);save(task/'analysis-input.json',view)
        row={'id':ident,'status':'failed','new_searches':0,'new_captures':0}
        metadata={k:copy.deepcopy(inputs[ident][k]) for k in ('as_of_utc','date_basis','historical_criteria_audit') if k in inputs[ident]}
        try:
            result=old.run_task(view,task/'old',metadata,dry_run)
            row['old']=result
            if dry_run:
                packet=load(task/'old/packet.json')
                # Check the largest existing old route, not just the smaller first state.
                state,_=old.select(packet,load(task/'old/first-state.json'),list(old.CHECKS),old.SECOND_BYTES)
                frozen_request={'model':'inception/mercury-decide:free','state':state,'questions':old.questions()}
            else:frozen_request=new.final_request(task/'old')
            row['new']=new.run_task(view,task/'new',frozen_request,dry_run=dry_run)
            row['status']='prepared' if dry_run else 'completed'
            row['final_coverage_identical']=True
        except Exception as exc:row['error']=str(exc)
        rows.append(row);save(output/'report.json',{'rows':rows,'protocol':identity['protocol'],
               'outcome_labels_loaded':False,'forecast_submissions':0})
        print({'id':ident,'status':row['status']},flush=True)
    if any(r['status']=='failed' for r in rows):raise RuntimeError('Holdout failure preserved; no replacement or quota reset')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--batch',type=int,required=True)
    for name in ('raw','supplements','output'):p.add_argument('--'+name,required=True)
    p.add_argument('--dry-run',action='store_true');a=p.parse_args()
    run(a.batch,a.raw,a.supplements,a.output,a.dry_run)
