"""Retry one material review against immutable saved bodies; no acquisition tools."""
import argparse
import copy
import hashlib
import os
import shutil
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.supplement import enhanced, material_review, need_ledger, binding_guard
from ForecastAgent.readers import material_passages
from ForecastAgent.supplement import requirement_contract
from ForecastAgent.tools.channels import PUBLISHERS


def run(parent, output, api_key, parent_run, *, need_batches=False):
    parent, output = Path(parent), Path(output)
    files = ['manifest.json', 'acquisition/bundle.json', 'intelligence-bundle.json', 'supplement/state.json']
    hashes = {f: hashlib.sha256((parent/f).read_bytes()).hexdigest() for f in files}
    initial = load(parent/'acquisition/bundle.json')
    overlay = load(parent/'intelligence-bundle.json')
    original = load(parent/'supplement/state.json')
    manifest = load(parent/'manifest.json')
    if any(a.get('status') == 'reserved' for key in ('material_model_attempts','material_reviews','attempts') for a in original.get(key, [])):
        raise ValueError('Unresolved parent reservations require investigation')
    identity = {'schema':'material-review-only-v1', 'parent_run':str(parent_run),
        'question_id':manifest['question_id'], 'parent_hashes':hashes,
        'review_implementation_sha256':hashlib.sha256(Path(material_review.__file__).read_bytes()).hexdigest(),
        'closure_guard_sha256':hashlib.sha256(Path(binding_guard.__file__).read_bytes()).hexdigest(),
        'material_passages_sha256':hashlib.sha256(Path(material_passages.__file__).read_bytes()).hexdigest(),
        'requirement_contract_sha256':hashlib.sha256(Path(requirement_contract.__file__).read_bytes()).hexdigest(),
        'publisher_catalog_sha256':digest(PUBLISHERS),
        'retry_seconds':300, 'additional_decisions':3 if need_batches else 1,
        'need_batches':need_batches, 'needs_per_batch':3 if need_batches else None,
        'generation':material_review.GENERATION}
    if (output/'identity.json').exists():
        if load(output/'identity.json') != identity:
            raise ValueError('Review retry identity changed')
        # Never silently repeat a request after interruption or job rerun.
        if (output/'result.json').exists():
            return load(output/'result.json')
        raise ValueError('Preserved review retry requires explicit audit before continuation')
    output.mkdir(parents=True, exist_ok=True)
    save(output/'identity.json', identity)
    state = copy.deepcopy(original)
    for path in (parent/'supplement').glob('material-model-*.json'):
        shutil.copy2(path, output/path.name)
    save(output/'state.json', state)
    caps = manifest['repair_caps']
    counts = enhanced.usage(initial, (), original)
    ledger = need_ledger.build(overlay, state, counts, caps)
    groups=[ledger['needs'][i:i+3] for i in range(0,len(ledger['needs']),3)] if need_batches else [ledger['needs']]
    sources=enhanced.plan(overlay)['sources']
    if len(groups)>3:
        raise ValueError('Review-only execution supports at most three batches; freeze a continuation before calling providers')
    before = len(state.get('material_model_attempts', []))
    reviews_before = len(state.get('material_reviews', []))
    review = material_review.callback(api_key, initial, max_reviews=min(4,reviews_before+len(groups)), review_retry_seconds=300)
    batches=[]
    for index,needs in enumerate(groups):
        payload=material_review.packet(overlay,{**ledger,'needs':needs},sources,
                                      coverage_v2=need_batches,balanced=need_batches)
        save(output/('input.json' if not need_batches else f'input-batch-{index+1}.json'),payload)
        start_review=len(state.get('material_reviews',[]))
        try:
            if not review.can_review(state):
                raise material_review.ReviewError('review_capacity_exhausted')
            result=material_review.bind(overlay,payload,review(payload,state,output))
            state['material_reviews'][-1].update(status='bound',result=result)
            batch_status='bound'
        except Exception as exc:
            result={'error':type(exc).__name__,'error_code':getattr(exc,'code','binding_or_transport_or_budget_failure'),'detail':str(exc)[:300]}
            batch_status='failed'
            for entry in state.get('material_reviews',[])[start_review:]:
                entry.update(status='invalid_or_failed',error_code=result['error_code'])
        batches.append({'batch':index+1,'need_ids':[n['id'] for n in needs],
                        'status':batch_status,'decision':result})
        save(output/'state.json',state)
        save(output/'batch-results.json',batches)
    status='bound' if all(b['status']=='bound' for b in batches) else 'partial' if any(b['status']=='bound' for b in batches) else 'failed'
    result=batches[0]['decision'] if not need_batches else {'batches':batches}
    save(output/'state.json', state)
    current = need_ledger.build(overlay, state, counts, caps)
    save(output/'material-needs.json', current)
    from ForecastAgent.supplement.delivery import build as build_delivery
    save(output/'material-delivery.json',build_delivery(current,state))
    report = {'schema':'material-review-only-result-v1', 'id':manifest['question_id'],
        'status':status, 'decision':result, 'needs_per_batch':3 if need_batches else None, 'parent_run':str(parent_run),
        'new_model_attempts':len(state.get('material_model_attempts', []))-before,
        'cumulative_model_attempts':len(initial.get('model_attempts', []))+len(state.get('material_model_attempts', [])),
        'original_provider_usage':counts, 'new_search_calls':0, 'new_fetch_calls':0,
        'forecasts_submitted':0, 'budget_reset':False, 'review_retry_window_seconds':300,
        'parent_unchanged':all(hashlib.sha256((parent/f).read_bytes()).hexdigest()==h for f,h in hashes.items()),
        'needs_before':[{k:n[k] for k in ('id','priority','target_material_captured')} for n in ledger['needs']],
        'needs_after':[{k:n[k] for k in ('id','priority','target_material_captured')} for n in current['needs']],
        'truth_verified':False}
    if not report['parent_unchanged']:
        raise ValueError('Parent evidence changed')
    save(output/'result.json', report)
    print(manifest['question_id'], status, report['new_model_attempts'], 'new model HTTP attempts', flush=True)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--parent', required=True); p.add_argument('--output', required=True)
    p.add_argument('--parent-run', required=True)
    p.add_argument('--need-batches',action='store_true')
    args = p.parse_args()
    run(args.parent, args.output, os.environ['OPENROUTER_API_KEY'], args.parent_run,need_batches=args.need_batches)
