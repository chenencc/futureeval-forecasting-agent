"""Offline structural replay over frozen materials; never rescore semantic labels."""
import argparse
import json
from pathlib import Path
from ForecastAgent.supplement.acquisition_contract import reading_packet, sha


def replay(manifest, max_chars=60000):
    rows = []
    for case in manifest['cases']:
        source = case['source']
        body = source['text']
        packet = reading_packet({source['url']: {'content': body}}, max_chars=max_chars)
        errors = []
        ranges = []
        for p in packet['passages']:
            if p['text'] != body[p['start']:p['end']] or p['body_sha256'] != sha(body):
                errors.append('source_identity_or_range_error')
            for c in p.get('context_spans', []):
                if c['text'] != body[c['start']:c['end']]:
                    errors.append('header_range_error')
            ranges.append((p['start'], p['end']))
        omitted_ranges = [(p['start'], p['end']) for p in packet['omitted']]
        all_ranges = sorted(ranges + omitted_ranges)
        if packet['inventory'][0]['readable'] and (not all_ranges or all_ranges[0][0] != 0 or
                all_ranges[-1][1] != len(body) or any(a[1] != b[0] for a, b in zip(all_ranges, all_ranges[1:]))):
            errors.append('unaccounted_saved_content')
        rows.append({'case_id': case['id'], 'source_sha256': sha(body),
            'saved_chars': len(body), 'readable': packet['inventory'][0]['readable'],
            'delivered_chars': packet['delivered_chars'], 'passage_count': len(packet['passages']),
            'omitted_unit_count': len(packet['omitted']), 'errors': errors})
    return {'schema': 'acquisition-contract-structural-replay-v1', 'cases': rows,
        'case_count': len(rows), 'failed_cases': sum(bool(r['errors']) for r in rows),
        'provider_calls': 0, 'new_searches': 0, 'semantic_improvement_measured': False,
        'scope': 'Exact saved-excerpt reading and omission accounting only; no new agent decisions.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    report = replay(manifest)
    report['manifest_sha256'] = sha(json.dumps(manifest, sort_keys=True))
    Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('case_count', 'failed_cases', 'provider_calls')}))
    if report['failed_cases']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
