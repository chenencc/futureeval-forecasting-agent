"""Read-only Gamma matching checks. No LLM, Tavily, forecasts or trades.

Run: python -m ForecastAgent.verify_polymarket
Cached responses are replayed on subsequent runs; use a fresh output directory
for a new point-in-time capture. This is discovery QA, not a historical backtest.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ForecastAgent.polymarket_match import parse_candidates

QUERIES = ['Royal Mail', 'Anthropic IPO', 'hurricane', '30 year treasury', 'Dow Jones']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('snapshots/polymarket-verification'))
    args = parser.parse_args()
    root = args.output.resolve()
    allowed = Path('snapshots').resolve()
    if not root.is_relative_to(allowed):
        raise ValueError('Output must be inside snapshots/')
    root.mkdir(parents=True, exist_ok=True)
    requests = 0

    def capture(name, url):
        nonlocal requests
        path = root / (name + '.json')
        if path.exists():
            snapshot = json.loads(path.read_text(encoding='utf-8'))
            if snapshot.get('url') != url:
                raise ValueError('Cached endpoint differs: ' + name)
        else:
            requests += 1
            with urlopen(Request(url, headers={'User-Agent': 'ForecastAgent-read-only-test/1.0'}), timeout=20) as response:
                raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError('Gamma response too large')
            snapshot = {'url': url, 'captured_at': datetime.now(UTC).isoformat(), 'payload': json.loads(raw)}
            path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        if 'payload' not in snapshot:
            raise ValueError('Failed cached response: ' + name)
        payload = snapshot['payload']
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return payload, {'path': str(path), 'url': url, 'captured_at': snapshot['captured_at'],
                         'sha256': hashlib.sha256(canonical).hexdigest()}

    def search_url(query):
        return 'https://gamma-api.polymarket.com/public-search?' + urlencode(
            {'q': query, 'limit_per_type': 10, 'events_status': 'active'})

    questions = json.loads((Path(__file__).parent / 'fixtures/retrieval_2026_five.json').read_text(encoding='utf-8'))
    baseline_file = root / 'baseline.json'
    baseline = json.loads(baseline_file.read_text(encoding='utf-8')) if baseline_file.exists() else []
    report = {'tested_at': datetime.now(UTC).isoformat(), 'mode': 'current_discovery_not_historical_backtest',
              'cases': [], 'positive_controls': []}
    positives = []
    for question, entity in zip(questions, QUERIES):
        for variant, query in [('full', question['question']), ('entity', entity)]:
            payload, provenance = capture(question['id'] + '-' + variant, search_url(query))
            candidates = parse_candidates(payload, question['question'])
            old = next((row['candidates'] for row in baseline if row['id'] == question['id'] and row['variant'] == variant), None)
            report['cases'].append({'id': question['id'], 'question': question['question'], 'query_type': variant,
                                    'query': query, 'snapshot': provenance, 'pagination': payload.get('pagination'),
                                    'old_candidates': old, 'candidates': candidates})
            assert all(not row['eligible_for_edge'] for row in candidates)
            if variant == 'full' and question['id'] in ('44448', '43703') and candidates:
                positives.append(candidates[0])
    # Known same-contract controls verify discovery recall and child-contract
    # identities. They do not establish equivalence with Metaculus questions.
    for seed in positives:
        payload, provenance = capture('positive-' + seed['market_id'], search_url(seed['market_title']))
        rows = parse_candidates(payload, seed['market_title'], limit=100)
        matching = next((row for row in rows if row['market_id'] == seed['market_id']), None)
        if matching is None or matching['match_score'] != 1:
            raise AssertionError('Exact-title search failed to recover child market: ' + seed['market_id'])
        detail, detail_provenance = capture('detail-' + seed['market_id'],
                                           'https://gamma-api.polymarket.com/markets/' + seed['market_id'])
        detail_row = parse_candidates({'markets': [detail]}, seed['market_title'])[0]
        assert detail_row['condition_id'] == matching['condition_id']
        assert detail_row['yes_token_id'] == matching['yes_token_id']
        assert detail_row['market_title'] == matching['market_title']
        report['positive_controls'].append({'market_id': seed['market_id'], 'status': 'passed',
             'search_snapshot': provenance, 'detail_snapshot': detail_provenance,
             'search_candidate': matching, 'detail_candidate': detail_row})
    report['new_http_requests'] = requests
    report['tavily_calls'] = report['model_calls'] = 0
    report['limits'] = ['Search is bounded to first page; hasMore means incomplete recall.',
                        'Similarity is lexical, not semantic equivalence.',
                        'No candidate is approved for edge or paper trading.',
                        'Current captures cannot be used as historical market probabilities.',
                        'Polymarket is not yet in the new retrieval tool registry.']
    (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for row in report['cases']:
        print(row['id'], row['query_type'], 'old=', len(row['old_candidates']) if row['old_candidates'] is not None else '?',
              'new=', len(row['candidates']), 'hasMore=', (row['pagination'] or {}).get('hasMore'))
    print('Positive controls:', len(report['positive_controls']), 'New public HTTP calls:', requests)
    print('Report:', root / 'report.json')


if __name__ == '__main__':
    main()
