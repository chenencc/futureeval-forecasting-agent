"""Frozen held-out saved-body acquisition review; no search, crawl or forecasts."""
import argparse
import gzip
import hashlib
import os
from pathlib import Path
from ForecastAgent.analysis.pilot import load,save
from ForecastAgent.supplement import enhanced,need_ledger,material_review
from ForecastAgent.experiments.material_review_only import run as review
from ForecastAgent.evidence.raw_capture import capture_report
from ForecastAgent.runtime.capacity import DEFAULT

MANIFEST=Path(__file__).with_name('MATERIAL_HELDOUT10.json')


def restore(case, downloaded):
    """Restore a hash-verified local archive when remote artifacts were pruned."""
    selected=next(r for r in load(MANIFEST)['cases'] if r['case']==case)
    repo=Path(__file__).parents[2]
    packed=(repo/selected['fixture']).read_bytes()
    if hashlib.sha256(packed).hexdigest()!=selected['fixture_sha256']:
        raise ValueError('Frozen compressed fixture changed')
    raw=gzip.decompress(packed)
    if hashlib.sha256(raw).hexdigest()!=selected['sha256']:
        raise ValueError('Restored snapshot differs from archived source')
    target=Path(downloaded)/selected['path']
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(raw)
    return target


def prepare(case, downloaded, output):
    frozen=load(MANIFEST)
    selected=next(r for r in frozen['cases'] if r['case']==case)
    if selected['id'] in frozen['excluded_ids']:
        raise ValueError('Repair case cannot enter held-out cohort')
    source=Path(downloaded)/selected['path']
    if hashlib.sha256(source.read_bytes()).hexdigest()!=selected['sha256']:
        raise ValueError('Frozen source bytes differ')
    b=load(source)
    if str(b['request'].get('id'))!=selected['id']:
        raise ValueError('Question identity mismatch')
    if any(a.get('status')=='reserved' for a in b.get('model_attempts',[])):
        raise ValueError('Unresolved initial model reservations')
    output=Path(output);parent=output/'parent';result_dir=output/'review'
    if parent.exists():
        raise ValueError('Existing held-out parent requires explicit continuation audit')
    # Never run acquisition or modify the original task. Legacy bundles without
    # capacity metadata use existing DEFAULT callback ceilings, not SOLID limits.
    cap=b.get('capacity',DEFAULT)
    caps={'tavily':cap['tavily_basic'],'exa':cap['exa_search'],'http':cap['initial_http'],'browser':6}
    state={'attempts':[], 'material_reviews':[], 'material_model_attempts':[]}
    task_root=Path(downloaded)/'tasks'/selected['id']
    # Import any physical material-review journal rather than silently restarting it.
    journals=list(task_root.rglob('state.json')) if task_root.exists() else []
    for p in journals:
        old=load(p)
        if old.get('material_model_attempts') or old.get('material_reviews'):
            raise ValueError('Prior material model ledger needs explicit migration')
    save(parent/'manifest.json',{'question_id':selected['id'],'repair_caps':caps,
        'source_sha256':selected['sha256'],'legacy_capacity_not_enlarged':True})
    save(parent/'acquisition/bundle.json',b)
    save(parent/'intelligence-bundle.json',b)
    save(parent/'supplement/state.json',state)
    ledger=need_ledger.build(b,state)
    packet=material_review.packet(b,ledger,enhanced.plan(b)['sources'])
    session=(b.get('sessions') or [{}])[-1]
    offline={'schema':'material-heldout10-offline-v1','case':selected,
        'original_model_http':len(b.get('model_attempts',[])),
        'original_decisions':session.get('model_decisions',0),
        'remaining_decisions':max(0,cap['model_decisions']-session.get('model_decisions',0)),
        'raw_capture':capture_report(b),'material_needs':ledger,
        'delivered_passage_count':len(packet['passages']),
        'saved_review_response_available':False,
        'old_model_assertion_comparison_available':False,
        'new_search_calls':0,'new_fetch_calls':0,'budget_reset':False,
        'manual_checklist':selected['required_materials'],
        'acceptance_status':'pending_manual_material_review'}
    save(output/'offline.json',offline)
    return selected,parent,result_dir


def run(case, downloaded, output, api_key):
    selected,parent,result_dir=prepare(case,downloaded,output)
    result=review(parent,result_dir,api_key,selected['run'])
    # Calculate the former axes-only closure against the exact same new response,
    # rather than comparing different models or differently covered documents.
    state=load(result_dir/'state.json');b=load(parent/'intelligence-bundle.json')
    latest=state.get('material_reviews',[])[-1:] if state.get('material_reviews') else []
    old_ids={r['need_id'] for rev in latest for r in rev.get('result',{}).get('bindings',[])
        if r.get('quote_bound') and all(r.get('axes',{}).get(a) is True for a in material_review.AXES)}
    ledger=load(result_dir/'material-needs.json')
    new_ids={n['id'] for n in ledger['needs'] if n['target_material_captured']}
    save(Path(output)/'paired-closure.json',{'id':selected['id'],'same_response_same_body_comparison':True,
        'axes_only_need_ids':sorted(old_ids),'guarded_need_ids':sorted(new_ids),
        'guard_prevented_closure_ids':sorted(old_ids-new_ids),
        'blocked_bindings':[{'need_id':n['id'],**r} for n in ledger['needs'] for r in n['blocked_material_bindings']],
        'measured_recall':None,'false_closure_rate':None,'false_rejection_rate':None,
        'manual_acceptance_pending':True,'new_model_decisions':1 if latest else 0})
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--case',type=int,required=True)
    p.add_argument('--parent',required=True);p.add_argument('--output',required=True)
    p.add_argument('--restore-fixture',action='store_true')
    p.add_argument('--offline-only',action='store_true');args=p.parse_args()
    if args.restore_fixture:restore(args.case,args.parent)
    if args.offline_only:prepare(args.case,args.parent,args.output)
    else:run(args.case,args.parent,args.output,os.environ['OPENROUTER_API_KEY'])
