"""Bounded original capture after discovery, without model or search calls.

The release acquisition, analysis prompts, distribution format and submission
interfaces stay independent. Raw sources and failed reservations are retained.
"""
import copy
import hashlib
import json
import re
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from ForecastAgent.supplement.stage import digest, save, now
from ForecastAgent.tavily_research import canonical_url
from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.acquisition.source_profile import catalog_role, observed_details, applicability

VERSION = 'release-eager-originals-v1'
FIELD = 'eager_original_capture'
PER_SEARCH = 2
AGENT_RESERVE = 2


def identity():
    return {'version': VERSION, 'per_search': PER_SEARCH, 'agent_fetch_reserve': AGENT_RESERVE,
        'extra_searches': 0, 'extra_models': 0, 'fetch_budget_shared': True,
        'files_sha256_lf': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()
            .replace(b'\r\n', b'\n')).hexdigest() for name in ('eager_capture.py', 'source_profile.py')}}


def enabled(task):
    value = task.bundle['request'].get(FIELD)
    if value is None:
        return False
    if value != identity():
        raise ValueError('Frozen eager-capture policy changed; no quota reset allowed')
    return True


def diagnostic(page, url):
    """Keep uncertain and short official/structured originals; reject shells only."""
    result = body_diagnostics(page.get('content', ''), documents=page.get('documents', []))
    shape = catalog_role(page, url)
    result['source_profile'] = shape
    result['qualifies_full_body'] = result['usable_text'] and shape.get('qualifies_full_body') is not False
    if shape.get('usable_text') is False:
        result.update(state=shape['role'], usable_text=False, qualifies_full_body=False)
    text = re.sub(r'\s+', ' ', page.get('content', '')).strip()
    # An explicit first-person location interstitial is not an article about one.
    blocked = len(text) < 1200 and bool(re.fullmatch(
        r'(?:sorry[,!]?\s*)?(?:this (?:site|page|service) is )?not available in your (?:country|region)\.?', text, re.I))
    if blocked:
        result.update(state='geographic_access_interstitial', usable_text=False, qualifies_full_body=False)
    result['readability_is_not_relevance_or_truth'] = True
    return result


def candidates(task):
    """Reuse release ranking, add only literal one-hop document destinations."""
    from ForecastAgent.runtime.source_frontier import unread_candidates
    from ForecastAgent.retrieval_sources import allowed_source
    b = task.bundle
    for url, page in list(b.get('pages', {}).items()):
        parent = b.get('source_leads', {}).get(canonical_url(url), {})
        if parent.get('depth', 0) >= 1 or parent.get('origin') in {'page_link', 'observed_document_detail'}:
            continue
        scope = applicability(page, url, parent, b['request'])
        if scope.get('automatic_followup_requires_scope_review'):
            continue
        for detail in observed_details(page, url):
            if detail.get('automatic_capture_eligible') and allowed_source(detail['url']):
                b.setdefault('source_leads', {}).setdefault(canonical_url(detail['url']), detail)
    attempted = {canonical_url(row['url']) for event in b.get('eager_capture_events', [])
                 for row in event.get('outcomes', [])}
    rows = [row for row in unread_candidates(b, limit=40) if row['canonical_url'] not in attempted]
    for row in rows:
        lead = b.get('source_leads', {}).get(row['canonical_url'], {})
        row['need_ids'] = sorted({n for batch in b.get('searches', []) + b.get('exa_searches', [])
            for hit in batch.get('results', []) if canonical_url(hit['url']) == row['canonical_url']
            for n in batch.get('need_ids', [])})
        row['observed_detail'] = lead.get('origin') == 'observed_document_detail'
        if row['observed_detail']:
            row['priority'] += 15
    return rows


def choose(rows, previous):
    hosts = {urlsplit(row['url']).hostname for row in previous}
    needs = {n for row in previous for n in row.get('need_ids', [])}
    return min(rows, key=lambda row: (not row.get('origin', '').startswith('question_'),
        not bool(set(row.get('need_ids', [])) - needs), urlsplit(row['url']).hostname in hosts,
        -row['priority'], row['canonical_url'])) if rows else None


def snapshot(bundle, root, stage):
    """Small immutable indexes share source objects instead of duplicating bodies."""
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    def object_ref(value):
        sha = digest(value); target = root/'objects'/(sha+'.json')
        if target.exists() and digest(json.loads(target.read_text(encoding='utf-8'))) != sha:
            raise ValueError('Saved capture object changed')
        if not target.exists(): save(target, value)
        return sha
    objects = {}
    for url, page in bundle.get('pages', {}).items():
        sha = object_ref(page)
        objects[url] = {'object_sha256': sha, 'captured_at': page.get('retrieved_at_utc'),
            'body_diagnostics': diagnostic(page, url)}
    value = {'schema': VERSION, 'stage': stage, 'frozen_at_utc': now(),
        'request_sha256': digest(bundle['request']), 'sources': objects,
        'context_objects': {name: object_ref(bundle.get(name, {} if name=='request' else []))
            for name in ('request', 'plan', 'searches', 'exa_searches')},
        'searches_sha256': digest(bundle.get('searches', [])),
        'exa_searches_sha256': digest(bundle.get('exa_searches', [])),
        'fetch_attempts': copy.deepcopy(bundle.get('fetch_attempts', [])),
        'semantic_completeness_verified': False, 'budget_reset': False}
    index = root/'packets'; index.mkdir(exist_ok=True)
    path = index/(f'{len(list(index.glob("*.json"))):04d}.json')
    save(path, value)
    return value


def eager(task):
    if not enabled(task) or task.cutoff or task.bundle.get('mode') != 'live' or not task.bundle.get('plan') or task.bundle.get('result'):
        return
    b = task.bundle; selected = []
    event = {'reserved_at_utc': now(), 'outcomes': [], 'new_model_calls': 0,
             'new_search_calls': 0, 'shared_fetch_cap': 8, 'agent_fetch_reserve': AGENT_RESERVE}
    b.setdefault('eager_capture_events', []).append(event); task.save()
    while len(selected) < PER_SEARCH and task.budget()['page_fetch_remaining'] > AGENT_RESERVE:
        row = choose(candidates(task), selected)
        if not row: break
        selected.append(row)
        before = len(b.get('fetch_attempts', []))
        try:
            task.execute('fetch_page', {'url': row['url']}, None)
            outcome = 'completed'
        except Exception as exc:
            outcome = 'failed'
            event['last_error_type'] = type(exc).__name__
        event['outcomes'].append({'url': row['url'], 'status': outcome,
            'origin': row.get('origin'), 'need_ids': row.get('need_ids', []),
            'fetch_reservation_added': len(b.get('fetch_attempts', [])) > before})
        task.save()
    snapshot(b, task.directory/'eager-capture', 'after_discovery')


@contextmanager
def adapter():
    """Apply only to explicitly frozen new release acquisition requests."""
    from ForecastAgent.runtime.retrieval import RetrievalTask
    original = RetrievalTask.execute
    def wrapped(task, name, args, key):
        search = enabled(task) and name in {'search_tavily', 'search_exa'}
        result = None
        try:
            result = original(task, name, args, key)
        finally:
            if search:
                try: eager(task)
                except Exception as exc:
                    task.bundle.setdefault('eager_capture_gaps', []).append({'error_type': type(exc).__name__,
                        'stage': 'after_discovery', 'reservations_retained': True})
                    task.save()
        if search and isinstance(result, dict):
            result = {**result, 'eager_capture': {
                'version': VERSION, 'outcomes': copy.deepcopy(task.bundle.get('eager_capture_events', [])[-1:]),
                'budget': task.budget(),
                'guidance': 'Originals are saved. Read a captured URL to inspect text without another fetch. Capture is not verification.'}}
        return result
    with patch.object(RetrievalTask, 'execute', wrapped):
        yield


def export(bundle, directory):
    """Quality annotations are derivative; native package/source text stays frozen."""
    child = copy.deepcopy(bundle)
    for url, page in child.get('pages', {}).items():
        page['body_diagnostics'] = diagnostic(page, url)
    existing = Path(directory)/'capture-export-package.json'
    if existing.exists():
        frozen = json.loads(existing.read_text(encoding='utf-8'))
        if frozen.get('eager_capture_summary', {}).get('parent_bundle_sha256') != digest(bundle):
            raise ValueError('Final capture parent changed')
        expected = copy.deepcopy(child)
        expected['eager_capture_summary'] = copy.deepcopy(frozen.get('eager_capture_summary'))
        if expected != frozen:
            raise ValueError('Frozen capture export changed')
        return frozen
    packet = snapshot(child, Path(directory)/'eager-final', 'after_independent_supplement')
    child['eager_capture_summary'] = {'version': VERSION, 'frozen_at_utc': packet['frozen_at_utc'],
        'readable_pages': sum(row['body_diagnostics']['usable_text'] for row in packet['sources'].values()),
        'full_body_pages': sum(row['body_diagnostics']['qualifies_full_body'] for row in packet['sources'].values()),
        'excluded_shell_urls': [u for u,r in packet['sources'].items() if not r['body_diagnostics']['usable_text']],
        'parent_bundle_sha256': digest(bundle), 'semantic_completeness_verified': False}
    return child
