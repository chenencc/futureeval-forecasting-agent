"""Identical-original-coverage Mercury comparison; no acquisition or labels."""
import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path
from ForecastAgent.analysis.pilot import load, save, digest, WARNING
from ForecastAgent.analysis import mercury_evidence_chain as old, mercury_nonbinary_trial as typed
from ForecastAgent.analysis.priority_reading import select as priority_select

FIXTURES = Path(__file__).parents[1]/'fixtures/enhanced_pair40'


def verify_packet(packet):
    for source in packet['sources']:
        spans = sorted((s for s in packet['evidence'] if s['source_id'] == source['source_id']), key=lambda s: s['start'])
        position = 0; body = ''
        for span in spans:
            if span['start'] != position or span['end']-span['start'] != len(span['text']):
                raise ValueError('Noncontiguous original span')
            position = span['end']; body += span['text']
        if hashlib.sha256(body.encode()).hexdigest() != source['body_sha256']:
            raise ValueError('Original body hash changed')


def route(item, name, folder, dry_run=False):
    folder = Path(folder); packet = item['packet']; registry = item['questions']
    selector = old.select if name == 'old' else lambda p, state=None, reasons=(), limit=old.FIRST_BYTES, decision_questions=None: priority_select(p, item['reading_hints'], state, reasons, limit, decision_questions)
    first, audit = selector(packet, decision_questions=registry)
    save(folder/'first-state.json', first); save(folder/'first-input-audit.json', audit)
    if dry_run: return {'status': 'prepared', 'request_bytes': audit['request_bytes']}
    response = old.call(first, folder/'first', registry)
    router = old.route if item['cohort'] == 'binary' else typed.route
    reasons = router(response)
    second, second_audit = selector(packet, first, reasons, old.SECOND_BYTES, registry) if reasons else (first, audit)
    kept = {s['evidence_id'] for s in first['evidence']}
    added = [s for s in second['evidence'] if s['evidence_id'] not in kept]
    gate = {'reasons': reasons, 'new_chars': sum(len(s['text']) for s in added),
            'second_required': bool(reasons) and sum(len(s['text']) for s in added) >= 900}
    save(folder/'routing.json', gate); final = response; second_error = None
    if gate['second_required']:
        save(folder/'second-state.json', second); save(folder/'second-input-audit.json', second_audit)
        try: final = old.call(second, folder/'second', registry)
        except RuntimeError as exc:
            second_error = str(exc)
            save(folder/'second-unavailable.json', {'error': second_error, 'validated_first_retained': True})
    result = {'status': 'completed', 'cohort': item['cohort'], 'routing': gate, 'second_error': second_error}
    if item['cohort'] == 'binary':
        p = final['answers']['event_yes']['noul']; result.update(probability_yes=p, clipped_probability_yes=min(.98, max(.02, p)))
    else:
        result.update(type=item['spec']['kind'], forecast=typed.forecast(final, item['spec']))
    result['http_attempts'] = len(list(folder.glob('*/http/*.json')))
    if result['http_attempts'] > 2: raise ValueError('Route lifetime request cap exceeded')
    save(folder/'result.json', result)
    return result


def run(batch, output, dry_run=False):
    if batch not in range(1, 9): raise ValueError('Frozen batch 1-8 required')
    manifest = load(FIXTURES/'manifest.json'); compressed = (FIXTURES/f'batch-{batch}.json.gz').read_bytes()
    if hashlib.sha256(compressed).hexdigest() != manifest['batches'][str(batch)]['sha256']:
        raise ValueError('Frozen source archive changed')
    items = json.loads(gzip.decompress(compressed)); output = Path(output)
    identity = {'protocol': 'enhanced-identical-body-pair40-v1', 'batch': batch,
                'input_archive_sha256': hashlib.sha256(compressed).hexdigest(), 'dry_run': dry_run,
                'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'selector_sha256': hashlib.sha256(Path(__file__).with_name('priority_reading.py').read_bytes()).hexdigest(),
                'retained_chain_sha256': hashlib.sha256(Path(old.__file__).read_bytes()).hexdigest(),
                'http_cap_per_route': 2, 'search_requests': 0, 'forecast_submissions': 0}
    if (output/'identity.json').exists() and load(output/'identity.json') != identity:
        raise ValueError('Frozen comparison changed; do not reset budgets')
    save(output/'identity.json', identity); rows = []
    for index, item in enumerate(items):
        folder = output/'tasks'/item['id']; verify_packet(item['packet'])
        if item['reading_hints']['packet_sha256'] != digest(item['packet']): raise ValueError('Reading hint identity changed')
        save(folder/'packet.json', item['packet']); save(folder/'questions.json', item['questions']); save(folder/'distribution-spec.json', item['spec'])
        order = ['old', 'new'] if index % 2 == 0 else ['new', 'old']
        row = {'id': item['id'], 'cohort': item['cohort'], 'call_order': order,
               'packet_sha256': digest(item['packet']), 'original_parent_sha256': item['original_parent_sha256'],
               'same_original_body_coverage': True}
        for name in order:
            try: row[name] = route(item, name, folder/name, dry_run)
            except Exception as exc:
                row[name] = {'status': 'failed', 'error': str(exc)}
                save(folder/name/'failure.json', row[name])
        row['status'] = 'completed' if all(row[n]['status'] in {'completed', 'prepared'} for n in order) else 'failed'
        rows.append(row)
        save(output/'report.json', {'rows': rows, 'protocol': identity['protocol'], 'model': 'inception/mercury-decide:free',
            'new_acquisition_requests': 0, 'labels_loaded': False, 'evaluation_warning': WARNING,
            'probability_questions_unchanged': True, 'same_original_body_coverage': True, 'forecast_submissions': 0})
        print(item['id'], row['status'], flush=True)
    if any(r['status'] == 'failed' for r in rows): raise RuntimeError('Comparison failures preserved; no automatic quota reset')


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--batch', type=int, required=True); p.add_argument('--output', required=True); p.add_argument('--dry-run', action='store_true')
    args = p.parse_args(); run(args.batch, args.output, args.dry_run)
