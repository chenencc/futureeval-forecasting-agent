"""Run an immutable release in a fresh isolated acquisition-to-analysis trial."""
import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def runtime_for(runtime, manifest):
    """Import only release code, even though this harness lives on dev_formal."""
    runtime = Path(runtime).resolve()
    for filename, expected in manifest['runtime_files_sha256'].items():
        actual = hashlib.sha256((runtime/filename).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        if actual != expected:
            raise ValueError('Release source changed: ' + filename)
    if (runtime/'.git').exists():
        commit = subprocess.check_output(['git', '-C', str(runtime), 'rev-parse', 'HEAD'], text=True).strip()
        if commit != manifest['release_commit']:
            raise ValueError('Release checkout commit differs')
    if any(k == 'ForecastAgent' or k.startswith('ForecastAgent.') for k in sys.modules):
        raise ValueError('Another ForecastAgent runtime was imported before release isolation')
    sys.path.insert(0, str(runtime))
    from ForecastAgent import agent
    from ForecastAgent.competition import live
    for name, module in list(sys.modules.items()):
        if name == 'ForecastAgent' or name.startswith('ForecastAgent.'):
            if getattr(module, '__file__', None) and not Path(module.__file__).resolve().is_relative_to(runtime):
                raise ValueError('Mixed development and release imports')
    return agent, live


def run(runtime, manifest_path, output, *, dry_run=False):
    manifest = load(manifest_path); output = Path(output).resolve()
    if output.exists():
        raise ValueError('Fresh trial requires a new directory; refuse to reset existing ledgers')
    if os.environ.get('FORECAST_SHARED_CACHE_ROOT'):
        raise ValueError('Fresh trial forbids shared capture cache')
    cases = manifest['cases']
    if len(cases) != 5 or len({c['id'] for c in cases}) != 5:
        raise ValueError('Exactly five frozen unique questions required')
    agent, live = runtime_for(runtime, manifest)
    output.mkdir(parents=True)
    save(output/'manifest.json', manifest)
    save(output/'identity.json', {'manifest_sha256': digest(manifest), 'release_commit': manifest['release_commit'],
         'harness_sha256': hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
         'new_experiment_budgets': True, 'old_ledgers_reset': False, 'provider_account_quotas_reset': False,
         'outcome_labels_loaded': False, 'forecast_submissions': 0, 'dry_run': dry_run})
    rows = []
    for case in cases:
        ident = case['id']; folder = output/'tasks'/ident
        question = {'id': int(ident), 'type': 'binary', 'title': case['question'],
                    'resolution_criteria': case['resolution_criteria'], 'description': case.get('background', ''),
                    'fine_print': case.get('fine_print', '')}
        for field in ('open_time', 'close_time', 'scheduled_close_time', 'scheduled_resolve_time', 'spot_scoring_time'):
            if field in case:
                question[field] = case[field]
        request = live.live_request({'title': case['question']}, question)
        save(folder/'request.json', request)
        row = {'id': ident, 'status': 'prepared' if dry_run else 'collecting', 'collection_executions': 0,
               'release_version': live.VERSION, 'request_sha256': digest(request),
               'opening_timestamp_available': bool(request.get('open_time'))}
        rows.append(row)
        save(output/'report.json', {'rows': rows, 'no_forecasts_submitted': True, 'outcome_labels_loaded': False})
        if dry_run:
            continue
        try:
            bundle = None
            for execution in range(3):
                row['collection_executions'] += 1
                save(output/'report.json', {'rows': rows, 'no_forecasts_submitted': True, 'outcome_labels_loaded': False})
                print(json.dumps({'id': ident, 'stage': 'collecting', 'execution': execution+1}), flush=True)
                bundle = agent.run_research(request, folder/'retrieval')
                result = bundle.get('result') or {}
                if result and not result.get('incomplete'):
                    break
                # Native worker continuation uses the same lifetime ledger. Only
                # dispatch/time ceilings qualify for immediate bounded continuation.
                if result.get('termination_reason') not in {'model_dispatch_budget', 'program_dispatch_limit', 'deadline'}:
                    break
            if not bundle or not bundle.get('result') or bundle['result'].get('incomplete'):
                raise RuntimeError('Release collection incomplete; preserved lifetime budgets')
            adopted = copy.deepcopy(bundle); adopted['request'].update(request)
            adopted['request']['as_of_utc'] = datetime.now(timezone.utc).isoformat()
            save(folder/'supplement-source.json', adopted)
            row.update(status='supplementing', collection_bundle_sha256=digest(bundle),
                saved_pages_before_supplement=len(bundle.get('pages', {})))
            print(json.dumps({'id': ident, 'stage': 'supplementing'}), flush=True)
            save(output/'report.json', {'rows': rows, 'no_forecasts_submitted': True, 'outcome_labels_loaded': False})
            overlay = live.supplement_bundle(adopted, folder, ident)
            save(folder/'analysis-input.json', overlay)
            row.update(status='analyzing', analysis_input_sha256=digest(overlay),
                       saved_pages_after_supplement=len(overlay.get('pages', {})))
            print(json.dumps({'id': ident, 'stage': 'analyzing'}), flush=True)
            save(output/'report.json', {'rows': rows, 'no_forecasts_submitted': True, 'outcome_labels_loaded': False})
            candidate = live.analyze(folder/'analysis-input.json', folder, ident)
            p = candidate['payload']['probability_yes']
            row.update(status='completed', probability_yes=p, selection=candidate['selection'],
                       candidate_sha256=digest(candidate))
        except Exception as exc:
            row.update(status='failed', error=str(exc)[:1000])
        save(folder/'outcome.json', row)
        save(output/'report.json', {'rows': rows, 'no_forecasts_submitted': True, 'outcome_labels_loaded': False})
        print(json.dumps({'id': ident, 'status': row['status'], 'p_yes': row.get('probability_yes'), 'error': row.get('error')}), flush=True)
    return rows


def evaluate(inputs, labels, output):
    """Join evaluation-only labels after immutable probabilities are audited."""
    import math
    from collections import Counter
    inputs, output = Path(inputs), Path(output)
    rows = load(inputs/'report.json')['rows']; manifest = load(inputs/'manifest.json')
    if [r['id'] for r in rows] != [c['id'] for c in manifest['cases']]:
        raise ValueError('Incomplete five-question terminal report')
    for row in rows:
        folder = inputs/'tasks'/row['id']
        if 'probability_yes' not in row:
            continue
        candidate = load(folder/'candidate.json')
        if digest(candidate) != row['candidate_sha256'] or candidate['payload']['probability_yes'] != row['probability_yes']:
            raise ValueError('Frozen release forecast changed')
        if digest(load(folder/'analysis-input.json')) != row['analysis_input_sha256']:
            raise ValueError('Frozen analysis package changed')
        mercury = folder/'mercury-v1.0.1'
        if candidate['selection'].startswith('mercury_'):
            stage = 'second' if candidate['selection'] == 'mercury_conditional_reread' else 'first'
            raw = load(mercury/stage/'response.json')['answers']['event_yes']['noul']
            if min(.98, max(.02, raw)) != row['probability_yes']:
                raise ValueError('Provider probability differs from candidate')
    frozen_hash = digest(rows)
    label_data = load(labels); lookup = {str(r['id']): r['resolved_to'] for r in label_data['labels']}
    scored = []
    for row in rows:
        if 'probability_yes' not in row:
            continue
        p = row['probability_yes']; y = lookup[row['id']]
        scored.append({**row, 'resolution': y, 'brier': (p-y)**2,
                       'log_loss': -(y*math.log(p)+(1-y)*math.log(1-p)), 'correct': (p>=.5)==bool(y)})
    report = {'requested': 5, 'scored': len(scored), 'coverage': len(scored)/5,
              'metrics': {k: sum(r[k] for r in scored)/len(scored) for k in ('brier', 'log_loss', 'correct')} if scored else {},
              'state_distribution': dict(Counter(r['status'] for r in rows)), 'results': scored,
              'failures': [r for r in rows if 'probability_yes' not in r], 'frozen_predictions_sha256': frozen_hash,
              'release_version': manifest['release_version'], 'release_commit': manifest['release_commit'],
              'labels_sha256': digest(label_data), 'label_provenance': label_data['provenance'],
              'forecast_submissions': 0, 'warning': manifest['evaluation_warning']}
    save(output/'metrics.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'evaluate']); parser.add_argument('--runtime')
    parser.add_argument('--manifest'); parser.add_argument('--inputs'); parser.add_argument('--labels')
    parser.add_argument('--output', required=True); parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.action == 'run':
        rows = run(args.runtime, args.manifest, args.output, dry_run=args.dry_run)
        if any(r['status'] == 'failed' for r in rows):
            raise SystemExit(1)
    else:
        print(json.dumps(evaluate(args.inputs, args.labels, args.output)['metrics'], indent=2))
