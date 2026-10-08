"""Paired source-reader trial over one frozen question and discovery frontier.

Common HTTP responses isolate parsing; alternating live browser captures measure
rendering separately. This harness has no model, search, analysis or submission
entry point. Failed and reserved operations are never automatically repeated.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
from contextlib import ExitStack
from pathlib import Path
import re
from unittest.mock import patch

from ForecastAgent.acquisition.nextgen import code_identity, metrics
from ForecastAgent.acquisition.pipeline import prepare, verify_baseline
from ForecastAgent.readers.browser import render_page as release_render
from ForecastAgent.readers.crawl4ai import enrich_html, render_page as candidate_render, require_backend
from ForecastAgent.supplement.stage import fetch_document, now, save
from ForecastAgent.runtime.task_lock import task_lock

ROOT = Path(__file__).resolve().parents[2]


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_capture(page):
    if hashlib.sha256(base64.b64decode(page['raw_response_base64'], validate=True)).hexdigest() != page['sha256']:
        raise ValueError('Original response/DOM hash mismatch')


def read_capture(root, record):
    path = (root / record['capture_file']).resolve()
    if not path.is_relative_to(root.resolve()) or file_hash(path) != record['capture_file_sha256']:
        raise ValueError('Capture escaped trial or changed')
    page = json.loads(path.read_text(encoding='utf-8'))
    verify_capture(page)
    return page


def validate(manifest):
    cases = manifest['cases']
    if not 1 <= len(cases) <= 5 or len({c['request']['id'] for c in cases}) != len(cases):
        raise ValueError('Select up to five distinct questions')
    if manifest['limits'] != {'common_http_per_question': 2, 'browser_per_arm_per_question': 2,
                              'browser_request_limit': 25, 'browser_timeout_ms': 20000,
                              'local_html_reparses_per_arm_per_question': 8}:
        raise ValueError('Trial allowances changed')
    for case in cases:
        prepare(case['request'])
        if not 1 <= len(case['sources']) <= 2 or len({s['url'] for s in case['sources']}) != len(case['sources']):
            raise ValueError('Each question has one or two frozen sources')
        if len(case['checks']) > 12:
            raise ValueError('Too many preregistered checks')
        for check in case['checks']:
            for expression in check['all_patterns']:
                if len(expression) > 300:
                    raise ValueError('Oversized matching expression')
                re.compile(expression)
        if file_hash(case['parent_file']) != case['parent_sha256']:
            raise ValueError('Frozen collection parent changed')


def freeze(manifest, browser_channel):
    validate(manifest)
    verify_baseline()
    names = ('ForecastAgent/acquisition/paired_sources.py', 'ForecastAgent/readers/browser.py',
             'ForecastAgent/readers/loader.py', 'ForecastAgent/supplement/stage.py')
    return {'manifest': manifest, 'browser_channel': browser_channel, 'candidate_sha256': code_identity(),
            'harness_and_release_sha256': {n: hashlib.sha256((ROOT/n).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for n in names},
            'baseline': 'v1.0.5', 'analysis_run': False, 'submitted': False}


def checks(page, rubric):
    # Search body/document text, never navigation URLs or candidate link scores.
    sections = [('content', page.get('content', ''))] + [
        ('document:'+str(i), d.get('page_content', '')) for i, d in enumerate(page.get('documents', []))]
    results = []
    for check in rubric:
        hits = []
        for location, body in sections:
            found = [re.search(p, body, re.I | re.S) for p in check['all_patterns']]
            if found and all(found):
                start, end = min(m.start() for m in found), max(m.end() for m in found)
                hits.append({'location': location, 'start_char': start, 'end_char': end,
                             'snippet': body[max(0, start-100):min(len(body), end+100)][:1600]})
        results.append({'id': check['id'], 'label': check['label'], 'matched': bool(hits),
                        'evidence': hits[:3], 'semantic_verification': False})
    return results


def prefer_capture(existing, incoming):
    """A partial render is an additional version, not a complete-body replacement."""
    if not incoming or not metrics(incoming)['readable']:
        return existing
    if (incoming.get('capture_status', {}).get('render_complete') is False and
            existing and metrics(existing)['readable'] and
            existing.get('capture_status', {}).get('render_complete') is not False):
        return existing
    return incoming


def export_preserved_versions(manifest, trial_root, output):
    """Repackage verified saved captures; never resume or rewrite the live trial."""
    validate(manifest)
    trial_root, output = Path(trial_root), Path(output)
    frozen = json.loads((trial_root/'identity.json').read_text(encoding='utf-8'))
    if frozen['manifest'] != manifest:
        raise ValueError('Export manifest differs from original trial')
    state = json.loads((trial_root/'state.json').read_text(encoding='utf-8'))
    output.mkdir(parents=True, exist_ok=True)
    export_identity = {'parent_identity_sha256': file_hash(trial_root/'identity.json'),
                       'parent_state_sha256': file_hash(trial_root/'state.json'),
                       'selection_policy': 'complete_body_before_partial_dom_v1',
                       'code_sha256': file_hash(Path(__file__)), 'network_calls': 0}
    with task_lock(output):
        if (output/'identity.json').exists() and json.loads((output/'identity.json').read_text(encoding='utf-8')) != export_identity:
            raise ValueError('Saved export identity changed')
        save(output/'identity.json', export_identity)
        selected = []
        for case in manifest['cases']:
            ident = case['request']['id']
            for arm in ('release', 'candidate'):
                package = json.loads(Path(case['parent_file']).read_text(encoding='utf-8'))
                version_rows = []
                for source in case['sources']:
                    url = source['url']
                    stages = ('common', 'reparse', 'candidate') if arm == 'candidate' else ('common', 'release')
                    primary = None
                    for stage in stages:
                        key = ident+':'+stage+':'+url
                        record = next((a for a in state['attempts'] if a['key'] == key), None)
                        if not record:
                            continue
                        page = read_capture(trial_root, record) if record.get('capture_file') else None
                        previous = primary
                        primary = prefer_capture(primary, page)
                        version_rows.append({'url': url, 'record_key': key, 'status': record['status'],
                            'capture_file': record.get('capture_file'), 'raw_sha256': record.get('raw_sha256'),
                            'selected_when_seen': primary is page and page is not None,
                            'complete_body_preserved': previous is primary and page is not None and
                                page.get('capture_status', {}).get('render_complete') is False})
                    if primary:
                        package['pages'][url] = primary
                    selected.append({'question_id': ident, 'arm': arm, 'url': url,
                        'raw_sha256': primary.get('sha256') if primary else None,
                        'metrics': metrics(primary) if primary else None,
                        'checks': checks(primary, case['checks']) if primary else []})
                package['paired_reader_lineage'] = {**export_identity, 'parent_bundle_sha256': case['parent_sha256'],
                    'arm': arm, 'request': case['request'], 'source_versions': version_rows,
                    'all_versions_root': str(trial_root.resolve()), 'analysis_run': False, 'submitted': False}
                save(output/'packages'/ident/(arm+'.json'), package)
        report = {'schema': 'paired_source_preserved_export_v1', 'identity': export_identity,
                  'selected': selected, 'provider_calls': {'models': 0, 'tavily': 0, 'exa': 0},
                  'analysis_run': False, 'submitted': False}
        save(output/'report.json', report)
        return report


def run(manifest, root, *, browser_channel='chromium', _fetch=None, _release=None, _candidate=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    identity = freeze(manifest, browser_channel)
    require_backend()
    with task_lock(root), ExitStack() as stack:
        if (root/'identity.json').exists():
            if json.loads((root/'identity.json').read_text(encoding='utf-8')) != identity:
                raise ValueError('Frozen inputs, code or browser changed; refuse silent restart')
        else:
            save(root/'identity.json', identity)
        state = json.loads((root/'state.json').read_text(encoding='utf-8')) if (root/'state.json').exists() else {'attempts': []}
        # Keep both Windows trial arms on exactly the same installed browser.
        # Production release code and Linux bundled Chromium are unchanged.
        if browser_channel != 'chromium':
            from playwright.sync_api import BrowserType
            launch = BrowserType.launch
            stack.enter_context(patch.object(BrowserType, 'launch',
                lambda bt, **kw: launch(bt, **dict(kw, channel=browser_channel))))

        def operation(key, callback, method):
            previous = next((a for a in state['attempts'] if a['key'] == key), None)
            if previous:
                return read_capture(root, previous) if previous.get('capture_file') else None
            record = {'key': key, 'method': method, 'status': 'reserved', 'started_at_utc': now()}
            state['attempts'].append(record)
            save(root/'state.json', state)
            try:
                page = callback()
                verify_capture(page)
                name = 'captures/'+hashlib.sha256(key.encode()).hexdigest()+'.json'
                save(root/name, page)
                record.update(status='captured', capture_file=name, capture_file_sha256=file_hash(root/name),
                              raw_sha256=page['sha256'], browser_audit=page.get('browser_audit'),
                              http_audit=page.get('http_audit'))
                return page
            except Exception as exc:
                record.update(status='failed', error=type(exc).__name__, detail=str(exc)[:600],
                              transport_audit=getattr(exc, 'audit', None))
            finally:
                record['finished_at_utc'] = now()
                save(root/'state.json', state)
            return None

        rows = []
        for index, case in enumerate(manifest['cases']):
            ident = case['request']['id']
            parent = json.loads(Path(case['parent_file']).read_text(encoding='utf-8'))
            packages = {arm: copy.deepcopy(parent) for arm in ('release', 'candidate')}
            for source in case['sources']:
                url = source['url']
                shared = operation(ident+':common:'+url, lambda: (_fetch or fetch_document)(url), 'common_http')
                for arm in ('release', 'candidate'):
                    page = shared
                    if arm == 'candidate' and shared and shared.get('content_type') in {'text/html', 'application/xhtml+xml'}:
                        page = operation(ident+':reparse:'+url, lambda: enrich_html(shared, query=case['request']['question']), 'local_reparse')
                    if page:
                        packages[arm]['pages'][url] = page
                    row = {'question_id': ident, 'source_id': source['id'], 'url': url, 'arm': arm,
                           'phase': 'identical_http_bytes', 'state': 'captured' if page else 'failed',
                           'metrics': metrics(page) if page else None,
                           'checks': checks(page, case['checks']) if page else []}
                    rows.append(row)
                if not source['browser']:
                    continue
                # Alternation reduces order effects; this is sequential live
                # rendering, not identical DOM or statistically powered A/B.
                order = ('release', 'candidate') if index % 2 == 0 else ('candidate', 'release')
                for arm in order:
                    renderer = (_release or release_render) if arm == 'release' else (_candidate or candidate_render)
                    kwargs = {'retrieved_at': now(), 'timeout_ms': 20000, 'request_limit': 25}
                    if arm == 'candidate':
                        kwargs.update(browser_channel=browser_channel, query=case['request']['question'])
                    page = operation(ident+':'+arm+':'+url, lambda: renderer(url, **kwargs), 'browser_'+arm)
                    existing = packages[arm]['pages'].get(url)
                    selected = prefer_capture(existing, page)
                    usable = selected is page and page is not None
                    if selected:
                        packages[arm]['pages'][url] = selected
                    rows.append({'question_id': ident, 'source_id': source['id'], 'url': url, 'arm': arm,
                                 'phase': 'live_browser', 'state': 'captured' if page else 'failed',
                                 'admitted_as_replacement': usable, 'metrics': metrics(page) if page else None,
                                 'checks': checks(page, case['checks']) if page else []})
            for arm, package in packages.items():
                package['paired_reader_lineage'] = {'parent_sha256': case['parent_sha256'], 'request': case['request'],
                    'arm': arm, 'source_scope': 'Frozen official-rule URLs and saved discovery leads only.',
                    'live_capture_is_cutoff_safe': False, 'prior_ledger_modified': False, 'analysis_run': False}
                save(root/'packages'/ident/(arm+'.json'), package)
            save(root/'intermediate-rows.json', rows)

        report = {'schema': 'crawl4ai_paired_five_v1', 'rows': rows, 'attempts': state['attempts'],
                  'provider_calls': {'models': 0, 'tavily': 0, 'exa': 0}, 'analysis_run': False, 'submitted': False,
                  'scope': 'Source reading over frozen discovery, not an end-to-end agent or score comparison.',
                  'limits': manifest['limits'], 'completed_at_utc': now()}
        save(root/'report.json', report)
        blind, mapping = [], []
        for row in rows:
            swap = hashlib.sha256(('crawl4ai-five:'+row['question_id']).encode()).digest()[0] % 2
            alias = ('A' if row['arm'] == 'release' else 'B') if not swap else ('B' if row['arm'] == 'release' else 'A')
            blind.append({k: v for k, v in dict(row, alias=alias).items() if k not in {'arm', 'metrics'}})
            mapping.append({'question_id': row['question_id'], 'alias': alias, 'arm': row['arm']})
        save(root/'blinded-review.json', sorted(blind, key=lambda r: (r['question_id'], r['source_id'], r['phase'], r['alias'])))
        save(root/'blinding-map.json', mapping)
        return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser-channel', choices=['chromium', 'chrome', 'msedge'], default='chromium')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--export-preserved', type=Path,
                        help='Repackage original saved captures into a separate output without network.')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    if args.export_preserved:
        result = export_preserved_versions(manifest, args.output, args.export_preserved)
        print(json.dumps({'selected_sources': len(result['selected']), 'network_calls': 0}))
        return
    if not args.execute:
        print(json.dumps(freeze(manifest, args.browser_channel), ensure_ascii=True))
        return
    result = run(manifest, args.output, browser_channel=args.browser_channel)
    print(json.dumps({'rows': len(result['rows']), 'attempts': len(result['attempts']), 'provider_calls': result['provider_calls']}))


if __name__ == '__main__':
    main()
