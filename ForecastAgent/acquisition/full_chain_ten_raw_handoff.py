"""Export preserved raw captures after a local context projection interruption.

This experiment-only handoff retains the incomplete collector result. It does
not resume collection, renew reservations, or certify evidence adequacy.
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

from ForecastAgent.acquisition import full_chain_ten as original
from ForecastAgent.acquisition import full_chain_ten_resume as prior
from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.local_sync import GitHub

AMENDMENT = original.ROOT/'ForecastAgent/experiments/full_chain_ten_raw_handoff_protocol.json'


def amendment():
    prior.amendment()
    a = load(AMENDMENT)
    if original.scoring.text_sha(__file__) != a['adapter_sha256_lf']:
        raise ValueError('Registered raw handoff implementation changed')
    if original.scoring.text_sha(original.PROTOCOL) != a['original_protocol_sha256_lf']:
        raise ValueError('Original scoring protocol changed')
    return a


def eligible(bundle):
    if prior.eligible(bundle):
        return True
    r = bundle.get('result') or {}
    return (r.get('status') == 'partial' and r.get('incomplete') is True and
            r.get('termination_reason') == 'context_projection_failure' and
            r.get('execution_report', {}).get('owner') == 'program' and
            r.get('execution_report', {}).get('interrupted') is True and
            bool(bundle.get('model_attempts')) and
            all(a.get('status') == 'received' for a in bundle['model_attempts']) and
            any(p.get('content') and p.get('body_diagnostics', {}).get('usable_text')
                for p in bundle.get('pages', {}).values()))


def restore(output, qid, run_id, client=None):
    a = amendment(); p, _ = original.protocol()
    if str(run_id) != str(a['exact_parent_run_id']) or qid not in p['question_ids']:
        raise ValueError('Unregistered exact restoration')
    client = client or GitHub(); repo = a['repo']
    run = client.api(f'repos/{repo}/actions/runs/{run_id}')
    if run['status'] != 'completed' or run['head_sha'] != a['exact_parent_commit'] or run['run_attempt'] != 1:
        raise ValueError('Exact parent is active or changed')
    meta = client.api(f'repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100')
    matches = [v for v in meta['artifacts'] if v['name'] == 'intelligent-full-chain-ten-'+qid]
    if len(matches) != 1 or matches[0]['expired']:
        raise ValueError('Exact parent archive missing; never start an empty ledger')
    artifact = matches[0]; folder = Path(output)/qid
    folder.mkdir(parents=True, exist_ok=True); target = folder/'raw-handoff-parent.zip'
    client.download(repo, artifact['id'], target)
    with zipfile.ZipFile(target) as saved:
        if sum(v.file_size for v in saved.infolist()) > 500_000_000:
            raise ValueError('Oversized exact parent archive')
        for item in saved.infolist():
            path = PurePosixPath(item.filename.replace('\\', '/'))
            if path.is_absolute() or '..' in path.parts or ':' in item.filename or (item.external_attr>>16)&0o170000 == 0o120000:
                raise ValueError('Unsafe archive member')
        saved.extractall(folder)
    receipt = {'source_run_id': run_id, 'artifact_id': artifact['id'],
               'archive_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
               'status': 'exact_state_restored', 'budget_reset': False}
    save(folder/'raw-handoff-restore-receipt.json', receipt)
    return receipt


def manifest(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def bridge(output, qid):
    a = amendment(); _, c = original.protocol()
    folder = Path(output)/qid; directory = folder/'acquisition'
    state = load(directory/'state.json')
    if state.get('stage') != 'collection':
        return False
    bp = directory/'collection/bundle.json'; raw = bp.read_bytes(); b = json.loads(raw)
    if not eligible(b):
        raise ValueError('Raw interruption is not a qualified local projection failure')
    request = next(r for r in c['requests'] if r['id'] == qid)
    frozen = original.pipeline.identity(request, True)
    if load(directory/'identity.json') != frozen or state.get('identity_sha256') != digest(frozen) or b['request'] != frozen['request']:
        raise ValueError('Frozen original capture identity changed')
    root = directory/'collection'
    for attempt in b.get('model_attempts', []):
        path = root/attempt['path']
        if not path.resolve().is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != attempt['sha256']:
            raise ValueError('Original provider journal changed')
        record = load(path)
        if record.get('status') != attempt['status']:
            raise ValueError('Original provider status disagrees')
    record = {'schema': 'saved-raw-interruption-handoff-v1', 'original_state': state,
              'original_result': b['result'], 'original_files_sha256': manifest(root),
              'bundle_file_sha256': hashlib.sha256(raw).hexdigest(),
              'amendment_sha256_lf': original.scoring.text_sha(AMENDMENT),
              'provider_counters': {k: len(b.get(k, [])) for k in
                                    ('model_attempts','searches','exa_searches','fetch_attempts','extract_attempts')},
              'model_calls_added': 0, 'search_calls_added': 0, 'budget_reset': False,
              'semantic_complete': False, 'submitted': False}
    save(folder/'raw-interruption-handoff.json', record)
    archive = directory/'parent.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as saved:
        saved.writestr('campaign.json', json.dumps({'tasks': {qid: {'status': 'closed_with_gaps'}}}))
        for source in sorted(root.rglob('*')):
            if source.is_file():
                saved.write(source, 'tasks/'+qid+'/'+source.relative_to(root).as_posix())
    with zipfile.ZipFile(archive) as saved:
        if saved.read('tasks/'+qid+'/bundle.json') != raw:
            raise ValueError('Raw parent archive changed')
    state.update(stage='supplement', parent_bundle_sha256=hashlib.sha256(raw).hexdigest(),
                 parent_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                 raw_collection_interrupted=True, raw_only_diagnostic_handoff=True,
                 interrupted=False)
    save(directory/'state.json', state)
    return True


def run(output, qid):
    amendment(); p, c = original.protocol()
    if qid not in p['question_ids']:
        raise ValueError('Unregistered question')
    folder = Path(output)/qid; sp = folder/'acquisition/state.json'
    if not sp.exists():
        raise ValueError('Exact preserved state is required; no fresh collection')
    if load(sp).get('stage') == 'collection':
        bridge(output, qid)
    if load(sp).get('stage') == 'supplement':
        request = next(r for r in c['requests'] if r['id'] == qid)
        original.pipeline.run(request, folder/'acquisition', supplement_network=True)
    result = original.run_case(output, qid)
    receipt = folder/'raw-interruption-handoff.json'
    if receipt.exists() and manifest(folder/'acquisition/collection') != load(receipt)['original_files_sha256']:
        raise ValueError('Downstream processing changed the original native files')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--question-id', required=True)
    parser.add_argument('--restore-run')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(); amendment()
    if args.restore_run:
        result = restore(args.output, args.question_id, args.restore_run)
    elif args.dry_run:
        result = original.run_case(args.output, args.question_id, dry_run=True)
    else:
        result = run(args.output, args.question_id)
        if result.get('stage') != 'complete' or any(v.get('probability_yes') is None for v in result['routes'].values()):
            raise RuntimeError('Incomplete preserved case; no implicit retry')
    print(json.dumps({k: result.get(k) for k in ('question_id','status','stage','error')}))


if __name__ == '__main__':
    main()
