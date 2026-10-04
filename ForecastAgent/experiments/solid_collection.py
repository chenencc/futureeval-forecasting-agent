"""Fresh, expanded-budget raw collection pilot with immutable provenance."""
import argparse
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path

from ForecastAgent.analysis.pilot import load, save, digest
from ForecastAgent.evidence.raw_capture import capture_report
from ForecastAgent.runtime.retrieval import run_retrieval
from ForecastAgent.runtime.capacity import SOLID, SOLID_V2
from ForecastAgent.supplement import enhanced

IDS = ['40695', '44126', '45183', '26754', '45045']
COHORTS = {'pilot5':IDS, 'repair3':['44126','26754','45045'], 'repair2':['26754','45045'],
    'gap2':['26754','45045'],
    'material_new5':['43658','44939','41480','43461','44727'],
    'new15':['43658','44431','44939','44128','43919',
             '41480','43822','44547','43844','44940',
             '43461','42630','44727','45048','44728']}
REPAIR_CAPS = {'tavily': 6, 'exa': 2, 'http': 64, 'browser': 12}
MATERIAL_COHORTS = {'gap2', 'material_new5'}
FIXTURES = Path(__file__).parents[1]/'fixtures/enhanced_pair40'


def case_indices(cohort, batch=1):
    if cohort not in COHORTS or type(batch) is not int or batch<1: raise ValueError('Invalid frozen cohort batch')
    start=(batch-1)*5
    if start>=len(COHORTS[cohort]):raise ValueError('Batch outside frozen cohort')
    return list(range(start+1,min(start+5,len(COHORTS[cohort]))+1))


def question(ident, experiment_id, profile='solid_v1'):
    manifest = load(FIXTURES/'manifest.json')
    for batch in range(1, 9):
        compressed = (FIXTURES/f'batch-{batch}.json.gz').read_bytes()
        if hashlib.sha256(compressed).hexdigest() != manifest['batches'][str(batch)]['sha256']:
            raise ValueError('Frozen question archive changed')
        for item in json.loads(gzip.decompress(compressed)):
            if item['id'] != ident:
                continue
            request = copy.deepcopy(item['packet']['question'])
            for key in ('resolution', 'resolved_to', 'freeze_datetime_value', 'assessment',
                        'probability', 'as_of_utc', 'historical_snapshot_bundle'):
                request.pop(key, None)
            request.update(id=ident, mode='live', pipeline='collection',
                acquisition_profile='collection_v3', acquisition_focus='raw_recall',
                collection_temporal_policy='current_information', budget_profile=profile,
                experiment_id=experiment_id, exa_search_policy='required')
            return request
    raise ValueError('Frozen pilot question not found')


def audit(bundle, overlay, state):
    """Mechanical coverage proxies; no outcome labels or analysis calls."""
    raw = capture_report(overlay)
    checks = enhanced.plan(overlay)
    counts = enhanced.usage(bundle, (), state)
    if any(counts[k] > REPAIR_CAPS[k] for k in counts):
        raise ValueError('Combined acquisition and repair cap exceeded')
    if len(bundle.get('extract_attempts', [])) > SOLID['extract_batches']:
        raise ValueError('Extract cap exceeded')
    model_attempts = len(bundle.get('model_attempts', []))+len(state.get('material_model_attempts', []))
    if model_attempts > SOLID['model_http_lifetime']:
        raise ValueError('Model lifetime cap exceeded')
    primary = [s['url'] for s in checks['sources'] if s['rule_primary']]
    missing_primary = [u for u in primary if not any(
        p.get('supplement_provenance', {}).get('actual_requested_url', key) == u and
        checks['body_assessments'][key]['eligible_for_evidence']
        for key, p in overlay.get('pages', {}).items())]
    return {'schema': 'solid_collection_quality_v1', 'raw_capture': raw,
        'source_diagnostics': checks['body_assessments'],
        'rule_primary_urls': primary, 'rule_primary_unreadable_or_missing': missing_primary,
        'remaining_gaps': overlay.get('enhanced_supplement', {}).get('remaining_gaps', []),
        'capture_identity_inventory': overlay.get('capture_identity_inventory', {}),
        'deferred_candidates': overlay.get('enhanced_supplement', {}).get('deferred_candidates', []),
        'cumulative_usage': counts, 'initial_usage': {
            'http': len(bundle.get('fetch_attempts', [])),
            'model_http': len(bundle.get('model_attempts', [])),
            'extract_batches': len(bundle.get('extract_attempts', []))},
        'new_supplement_attempts': len(state.get('attempts', [])),
        'supplement_model_http':len(state.get('material_model_attempts', [])),
        'total_model_http':model_attempts,
        'budget_violation': False, 'semantic_recall_verified': False,
        'quality_requires_blind_source_review': True,
        'scope': 'Capture integrity and observable source coverage, not forecasting accuracy.'}


def run(case, output, experiment_id, prepare_only=False, cohort='pilot5'):
    if cohort not in COHORTS or case not in range(1,len(COHORTS[cohort])+1) or not experiment_id:
        raise ValueError('A frozen cohort case and explicit experiment ID required')
    output = Path(output)
    ident = COHORTS[cohort][case-1]
    profile='solid_v1' if cohort=='pilot5' else 'solid_v2'
    capacity=SOLID if profile=='solid_v1' else SOLID_V2
    request = question(ident, experiment_id, profile)
    if cohort in MATERIAL_COHORTS: request['collection_workflow']='material-gap-v1'
    if cohort=='material_new5': request['collection_stage_allocation']='material-reserve-v1'
    manifest = {'protocol': 'solid-raw-collection-v1', 'experiment_id': experiment_id,
        'question_id': ident, 'request_sha256': digest(request), 'capacity': capacity,
        'cohort':cohort,'frozen_cohort_ids':COHORTS[cohort],
        'material_max_depth':2 if profile=='solid_v1' else 3,
        'repair_caps': REPAIR_CAPS, 'fresh_authorized_allowance': True,
        'old_ledgers_imported': False, 'old_ledgers_deleted': False,
        'model_policy': 'Ultra preferred; Super after two consecutive service failures',
        'secret_name': 'OPENROUTER2', 'forecast_submissions': 0,
        'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'warning': 'Current-information acquisition of historical questions; not a leakage-free backtest.'}
    if (output/'manifest.json').exists() and load(output/'manifest.json') != manifest:
        raise ValueError('Frozen fresh experiment changed; do not reset counters')
    save(output/'manifest.json', manifest)
    save(output/'question.json', request)
    checklist=load(Path(__file__).with_name('COLLECTION_COHORTS.json'))
    save(output/'acceptance-checklist.json',{'id':ident,'cohort':cohort,
        'required_materials':checklist.get('repair3' if cohort in {'repair2','gap2'} else cohort,{}).get(ident,[]),
        'scope':'Predeclared acquisition checklist, not resolution labels or semantic acceptance.'})
    if prepare_only:
        return manifest
    try:
        bundle = run_retrieval(request, output/'acquisition', os.environ['TAVILY_API_KEY'],
                               os.environ['OPENROUTER_API_KEY'])
        from ForecastAgent.supplement.gap_search import callback
        search = callback(os.environ['TAVILY_API_KEY'],os.environ.get('EXA_API_KEY')) if cohort in MATERIAL_COHORTS else None
        from ForecastAgent.supplement.material_review import callback as material_callback
        agent = material_callback(os.environ['OPENROUTER_API_KEY'], bundle) if cohort in MATERIAL_COHORTS else None
        overlay = enhanced.run(bundle, output/'supplement', network=True,search=search,
                               caps=REPAIR_CAPS, max_link_depth=manifest['material_max_depth'], material_agent=agent)
        # A raw acquisition deliverable retains blocked/context bodies as well.
        # Their technical diagnostics remain visible; nothing becomes evidence
        # merely because it has been restored to the raw inventory.
        for url, page in overlay.get('supplement_excluded_pages', {}).items():
            overlay['pages'].setdefault(url, page)
        state = load(output/'supplement/state.json')
        save(output/'intelligence-bundle.json', overlay)
        quality = audit(bundle, overlay, state)
        save(output/'quality.json', quality)
        save(output/'report.json', {'status': 'captured_with_recorded_gaps', 'id': ident,
            'quality': quality, 'forecast_submissions': 0, 'analysis_calls': 0})
        print(ident, quality['raw_capture']['readable_body_count'], 'readable bodies', flush=True)
    except Exception as exc:
        save(output/'failure.json', {'id': ident, 'error': str(exc),
            'state_preserved': True, 'budget_reset': False})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', type=int, required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--experiment-id', required=True)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--cohort',choices=sorted(COHORTS),default='pilot5')
    args = parser.parse_args()
    run(args.case, args.output, args.experiment_id, args.prepare_only,args.cohort)
