"""One durable acquisition package; never analyze truth or submit forecasts."""
import copy
import json
import zipfile
from pathlib import Path

from ForecastAgent.agent import run_research
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.evidence.collection_handoff import inspect, repair_input, analysis_view, LEDGERS
from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.supplement.stage import run as supplement, analysis_overlay

PROTOCOL = 'unified-acquisition-v1'
DEFAULT_LIMITS = {'http': 10, 'browser': 4, 'saved_reparse': 8, 'executions': 3}


def disclose_coverage(package, bundle):
    """Keep collector-declared gaps distinct from failed captures and unread leads."""
    result = bundle.get('result') or {}
    declared = list(dict.fromkeys(result.get('agent_declared_gaps', [])))
    unread = list(dict.fromkeys(result.get('unread_urls', [])))
    out = copy.deepcopy(package)
    out['acquisition_coverage'] = {'agent_declared_gaps': declared,
        'unread_candidate_urls': unread, 'unread_candidate_count': len(unread),
        'unread_candidates_are_not_all_required_sources': True,
        'agent_declarations_are_not_verified_findings': True,
        'full_recall_verified': False}
    if declared:
        out['state'] = 'collected_with_gaps'
    return out


def collect(request, folder, *, network=True, limits=None):
    """Resume identical input and reservations, returning a checked body package.

    The original collector owns search/model quotas. Repair only reads saved raw
    responses and discovered public URLs. A partial collector output is preserved
    and can enter review, with its limitations explicitly attached.
    """
    limits = dict(DEFAULT_LIMITS if limits is None else limits)
    if set(limits) != set(DEFAULT_LIMITS) or any(type(v) is not int or v < 0 for v in limits.values()):
        raise ValueError('invalid_repair_limits')
    if limits['http'] > 10 or limits['browser'] > 4 or limits['saved_reparse'] != 8 or not 1 <= limits['executions'] <= 3:
        raise ValueError('repair_limits_exceed_authorized_bounds')
    if request.get('pipeline') != 'collection' or request.get('mode') != 'live' or not str(request.get('id', '')).isdecimal():
        raise ValueError('live_collection_with_numeric_identity_required')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    identity = {'protocol': PROTOCOL, 'request_sha256': digest(request), 'network': network, 'limits': limits}
    with task_lock(folder):
        manifest = folder / 'manifest.json'
        if manifest.exists() and load(manifest) != identity:
            raise ValueError('frozen_acquisition_input_or_limits_changed')
        save(manifest, identity)
        final = folder / 'package.json'
        if final.exists():
            package = load(final)
            if digest(load(folder / 'analysis-input.json')) != package['analysis_input_sha256']:
                raise ValueError('acquisition_output_changed')
            raw = load(folder / 'raw/bundle.json')
            if digest(raw) != package['raw_bundle_sha256']:
                raise ValueError('raw_acquisition_parent_changed')
            disclosed = disclose_coverage(package, raw)
            if disclosed != package:
                save(folder / 'coverage-disclosure.json', {'previous_package_sha256': digest(package),
                    'updated_package_sha256': digest(disclosed), 'raw_and_analysis_bodies_unchanged': True,
                    'provider_calls': 0, 'budgets_reset': False})
                save(final, disclosed)
            package = disclosed
            return package
        raw_path = folder / 'raw/bundle.json'
        executions_path = folder / 'executions.json'
        executions = load(executions_path) if executions_path.exists() else []
        # Never repeat acquisition after inspection/repair has frozen its parent.
        if not (folder / 'inspection.json').exists():
            bundle = load(raw_path) if raw_path.exists() else None
            terminal = bundle and bundle.get('result') and not bundle['result'].get('incomplete')
            if not terminal and network and len(executions) < limits['executions']:
                executions.append({'status': 'reserved'})
                save(executions_path, executions)
                try:
                    bundle = run_research(request, folder / 'raw')
                    executions[-1].update(status='returned', session_state=bundle.get('session_state'))
                except Exception as exc:
                    executions[-1].update(status='failed', error=str(exc)[:300])
                    if not raw_path.exists():
                        raise
                    bundle = load(raw_path)
                finally:
                    save(executions_path, executions)
            if not raw_path.exists():
                raise RuntimeError('no_saved_acquisition_bundle')
            bundle = load(raw_path)
            save(folder / 'inspection.json', inspect(bundle))
        bundle = load(raw_path)
        if bundle['request'] != request:
            raise ValueError('saved_question_changed')
        inspection = load(folder / 'inspection.json')
        repair_parent = repair_input(bundle, inspection)
        if not isinstance(repair_parent.get('result'), dict):
            repair_parent['result'] = {'incomplete': True, 'gaps': ['Collector has no final result; original bundle preserved.']}
        save(folder / 'repair-input.json', repair_parent)
        archive = folder / 'repair-parent.zip'
        if not archive.exists():
            ident = str(request['id'])
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                z.writestr('campaign.json', json.dumps({'tasks': {ident: {'status': 'closed_with_gaps'}}}))
                z.writestr(f'tasks/{ident}/bundle.json', json.dumps(repair_parent))
        ident = str(request['id'])
        supplement(archive, folder / 'repair', [ident], network=network,
                   http_limit=limits['http'], browser_limit=limits['browser'])
        overlay = analysis_overlay(repair_parent, folder / 'repair', ident)
        sidecar = load(folder / 'repair/tasks' / ident / 'supplement.json')
        overlay['supplement_lineage']['remaining_gaps'] = sidecar['remaining_gaps']
        if overlay['request'] != request or any(digest(overlay.get(k, [])) != inspection['ledger_sha256'][k] for k in LEDGERS):
            raise ValueError('repair_changed_original_reservations')
        view = analysis_view(overlay, digest(bundle))
        collector_complete = bool(bundle.get('result')) and not bundle['result'].get('incomplete')
        if not collector_complete:
            view['gaps'].append('Original collection stopped incomplete; saved bodies remain reviewable.')
        if any(e['status'] == 'failed' for e in executions):
            view['gaps'].append('Original collection encountered a provider/runtime failure; see executions.json.')
        save(folder / 'analysis-input.json', view)
        package = {'schema': PROTOCOL, 'question_id': ident, 'raw_bundle_sha256': digest(bundle),
            'analysis_input_sha256': digest(view), 'collector_complete': collector_complete,
            'raw_page_count': len(bundle.get('pages', {})), 'readable_unique_pages': len(view['pages']),
            'excluded_pages': len(view['handoff_excluded_pages']), 'repair_captures': len(sidecar['captures']),
            'repair_attempts': copy.deepcopy(sidecar['attempts']), 'gaps': view['gaps'],
            'consumption': {k: len(bundle.get(k, [])) for k in LEDGERS},
            'repair_provider_calls': sidecar['provider_calls'], 'original_ledgers_preserved': True,
            'truth_verified': False, 'forecast_submissions': 0,
            'state': 'collected_with_gaps' if view['gaps'] or not collector_complete else 'collected'}
        package = disclose_coverage(package, bundle)
        save(final, package)
        return package
