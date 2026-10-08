"""Experimental source tools: saved reading and explicitly selected resource follow-up.

No model, search, forecast or production agent registration occurs here.
"""
import argparse
import hashlib
import json
from pathlib import Path

from ForecastAgent.acquisition.nextgen import code_identity
from ForecastAgent.readers.material_structure import discover_resources, source_sections, structured_rows, saved_bytes
from ForecastAgent.readers.crawl4ai import render_page
from ForecastAgent.runtime.task_lock import task_lock
from ForecastAgent.supplement.stage import fetch_document, now, save


def inspect(snapshot):
    saved_bytes(snapshot)
    if snapshot.get('content_type') in {'text/html', 'application/xhtml+xml'}:
        return {'sections': source_sections(snapshot), 'resources': discover_resources(snapshot)}
    return {'structured_data': structured_rows(snapshot)}


def follow_observed(snapshot, selections, root, *, browser_channel='chromium', _fetch=None, _render=None):
    """Follow at most two observed URLs, with at most one additional browser render.

    Frozen identity prevents changed selections or refreshed budgets on resume.
    Reserved or failed operations are retained and never automatically retried.
    The caller assigns one root per parent snapshot; cross-parent budgets remain
    the enclosing agent's responsibility. This is a reader budget, not a search.
    """
    observed = {r['url']: r for r in discover_resources(snapshot)['resources']}
    if not 1 <= len(selections) <= 2 or len({s['url'] for s in selections}) != len(selections):
        raise ValueError('Select one or two distinct observed resources')
    if sum(bool(s.get('render')) for s in selections) > 1:
        raise ValueError('Only one browser render is allowed')
    for s in selections:
        if set(s) - {'url', 'render'} or s['url'] not in observed or type(s.get('render', False)) is not bool:
            raise ValueError('Resource must be observed in the verified parent snapshot')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    identity = {'schema': 'observed_resource_follow_v1', 'parent_url': snapshot['url'],
                'parent_source_sha256': snapshot['sha256'], 'parent_snapshot_sha256':
                hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                'selections': selections, 'code_sha256': code_identity(),
                'tool_sha256': hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
                'browser_channel': browser_channel, 'limits': {'http': 2, 'browser': 1,
                'browser_requests': 25, 'browser_timeout_ms': 20000, 'automatic_retries': 0}}
    with task_lock(root):
        ipath, spath = root/'identity.json', root/'state.json'
        if ipath.exists() and json.loads(ipath.read_text(encoding='utf-8')) != identity:
            raise ValueError('Follow-up identity changed; refuse budget restart')
        save(ipath, identity)
        state = json.loads(spath.read_text(encoding='utf-8')) if spath.exists() else {'attempts': []}
        for selected in selections:
            for method in ['http'] + (['browser'] if selected.get('render') else []):
                url = selected['url']
                key = method+':'+url
                existing = next((r for r in state['attempts'] if r['key'] == key), None)
                if existing:
                    if existing.get('capture_file'):
                        p = (root/existing['capture_file']).resolve()
                        if not p.is_relative_to(root.resolve()) or hashlib.sha256(p.read_bytes()).hexdigest() != existing['file_sha256']:
                            raise ValueError('Follow-up archive changed')
                    continue
                record = {'key': key, 'url': url, 'method': method, 'status': 'reserved',
                          'reserved_at_utc': now(), 'parent_observation': observed[url]}
                state['attempts'].append(record)
                save(spath, state)
                try:
                    page = (_fetch or fetch_document)(url) if method == 'http' else (_render or render_page)(
                        url, retrieved_at=now(), timeout_ms=20000, request_limit=25, browser_channel=browser_channel)
                    page['observed_resource_lineage'] = {'parent_source_sha256': snapshot['sha256'],
                        'parent_snapshot_sha256': identity['parent_snapshot_sha256'],
                        'parent_retrieved_at_utc': snapshot.get('retrieved_at_utc'), 'observation': observed[url]}
                    # A parse gap never discards a successfully archived body.
                    try:
                        projection = inspect(page)
                    except (ValueError, TypeError, RecursionError) as exc:
                        projection = {'state': 'projection_gap', 'error': type(exc).__name__}
                    out = root/'captures'/(hashlib.sha256(key.encode()).hexdigest()+'.json')
                    save(out, {'snapshot': page, 'reading': projection})
                    record.update(status='captured', capture_file=str(out.relative_to(root)),
                        file_sha256=hashlib.sha256(out.read_bytes()).hexdigest(), raw_sha256=page['sha256'],
                        http_audit=page.get('http_audit'), browser_audit=page.get('browser_audit'))
                except Exception as exc:
                    record.update(status='failed', error=type(exc).__name__, detail=str(exc)[:500],
                                  failure_audit=getattr(exc, 'audit', None))
                record['finished_at_utc'] = now()
                save(spath, state)
        return state


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--selections', type=Path)
    p.add_argument('--browser-channel', choices=['chromium', 'chrome', 'msedge'], default='chromium')
    a = p.parse_args()
    snapshot = json.loads(a.input.read_text(encoding='utf-8'))
    if a.selections:
        result = follow_observed(snapshot, json.loads(a.selections.read_text(encoding='utf-8')),
                                 a.output, browser_channel=a.browser_channel)
    else:
        result = inspect(snapshot)
        save(a.output, result)
    print(json.dumps({'saved': str(a.output), 'operations': len(result.get('attempts', [])),
                      'model_calls': 0, 'search_calls': 0}))


if __name__ == '__main__':
    main()
