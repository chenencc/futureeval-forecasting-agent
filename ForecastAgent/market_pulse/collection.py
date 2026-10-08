"""Versioned financial routing overlay on the checksum-verified v105 collector.

The overlay is process-local, acquisition-only, and restored after execution.
Its code hashes are part of the frozen request; existing ledgers cannot migrate
silently. No release dependency, model routing or resource limit is modified.
"""
import copy
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit
import zipfile

from ForecastAgent.market_pulse.financial import issuer_profile, instruction, research_contract, url_scope, page_scope
from ForecastAgent.market_pulse.quality import diagnostics, page_diagnostics, BASE_DIAGNOSTICS
from ForecastAgent.supplement.stage import digest

ACTIVE = threading.Lock()


def policy_hashes():
    folder = Path(__file__).parent
    return {name: hashlib.sha256((folder / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for name in ('financial.py', 'quality.py', 'collection.py')}


def prepare(request):
    result = copy.deepcopy(request)
    profile = issuer_profile(result)
    policy = {'version': profile['schema'], 'source_sha256': policy_hashes(),
              'issuer': profile, 'research': research_contract(profile)}
    if 'financial_acquisition_policy' in result and result['financial_acquisition_policy'] != policy:
        raise ValueError('Financial acquisition policy changed; an explicit new experiment is required')
    result['financial_acquisition_policy'] = policy
    return result


def scoped_pages(bundle, profile):
    """Keep original bytes in an audit branch, outside active evidence pages."""
    result = copy.deepcopy(bundle)
    rejected = result.setdefault('financial_audit_pages', {})
    for url, page in list(result.get('pages', {}).items()):
        page['body_diagnostics'] = page_diagnostics(page)
        page['financial_source_scope'] = page_scope(url, page, profile)
        if not page['body_diagnostics']['usable_text'] or not page['financial_source_scope']['eligible_for_target']:
            rejected[url] = result['pages'].pop(url)
    result['financial_acquisition_audit'] = {'policy': profile['schema'],
        'active_pages': len(result.get('pages', {})), 'excluded_pages': len(rejected),
        'excluded_bytes_retained': True, 'relevance_verified': False}
    result['financial_audit_excerpts'] = [e for e in result.get('excerpts', []) if e.get('url') in rejected]
    result['excerpts'] = [e for e in result.get('excerpts', []) if e.get('url') not in rejected]
    return result


def overlay(bundle, supplement_root, ident):
    """Validate old sidecar counters against actual bytes; never trust readable flags."""
    folder = Path(supplement_root) / 'tasks' / ident
    child = json.loads((folder / 'supplement.json').read_text(encoding='utf-8'))
    if child['task_id'] != ident or child['parent_bundle_json_sha256'] != digest(bundle):
        raise ValueError('Supplement does not belong to this parent bundle')
    result = copy.deepcopy(bundle)
    for url, entry in child['captures'].items():
        path = (folder / entry['file']).resolve()
        if not path.is_relative_to(folder.resolve()):
            raise ValueError('Invalid capture path')
        page = json.loads(path.read_text(encoding='utf-8'))
        if digest(page) != entry['json_sha256']:
            raise ValueError('Supplement capture hash mismatch')
        previous = bundle.get('pages', {}).get(url)
        page['body_diagnostics'] = page_diagnostics(page, previous)
        # A failed supplement must not replace a healthy original capture.
        if page['body_diagnostics']['usable_text']:
            result.setdefault('pages', {})[url] = page
        elif not previous or not page_diagnostics(previous)['usable_text']:
            result.setdefault('pages', {})[url] = page
        else:
            result.setdefault('financial_supplement_rejections', {})[url] = page
    profile = issuer_profile(bundle['request'])
    result = scoped_pages(result, profile)
    usable = set(result.get('pages', {}))
    result['supplement_lineage'] = copy.deepcopy(child['analysis_handoff'])
    result['supplement_lineage']['financial_rechecked'] = True
    result['capture_gaps'] = [{'url': url, 'state': page['body_diagnostics']['state'],
        'source_scope': page['financial_source_scope']['state'],
        'scope': 'Saved body or issuer routing gap; no absence of the forecast event inferred.'}
        for url, page in result['financial_audit_pages'].items()]
    result['capture_gaps'] += [r for r in child.get('remaining_gaps', [])
        if r['url'] not in usable and url_scope(r['url'], profile) != 'other_issuer']
    result['supplement_lineage']['remaining_gap_count_rechecked'] = len(result['capture_gaps'])
    # Parent gaps stay visible even when active evidence or sidecar counters change.
    result['gaps'] = copy.deepcopy(bundle.get('gaps', bundle.get('result', {}).get('gaps', [])))
    if result['capture_gaps']:
        result['gaps'].append({'code': 'financial_capture_gaps', 'capture_gaps': result['capture_gaps']})
    return result


@contextmanager
def acquisition_policy():
    """Use only in one acquisition child process; reject concurrent activation."""
    if not ACTIVE.acquire(blocking=False):
        raise RuntimeError('Financial acquisition policy requires an isolated child process')
    replacements = []
    try:
        from ForecastAgent.runtime import retrieval, guidance, task_protocol, source_frontier, gap_repair
        from ForecastAgent.supplement import stage
        original_frontier = source_frontier.unread_candidates
        original_inventory = gap_repair.inventory
        original_parse = stage.parse_saved
        original_system = guidance.collection_system
        original_view = task_protocol.task_view
        original_catalog = retrieval.RetrievalTask.catalog
        original_page_view = retrieval.RetrievalTask.page_view
        def checked_diagnostics(content, **kwargs):
            result = diagnostics(content, **kwargs)
            key = hashlib.sha256(content.encode()).hexdigest()
            # Bind negative lineage to this parsed document version, never
            # globally to text that another successful response might share.
            for document in kwargs.get('documents', ()):
                lineage = document.get('metadata', {}).get('financial_same_version_rejection') or {}
                if lineage.get('content_sha256') == key and lineage.get('raw_sha256'):
                    result.update(copy.deepcopy(lineage['diagnostic']))
                    break
            return result

        def frontier(bundle, limit=20):
            profile = issuer_profile(bundle['request'])
            # Filter before truncation; rejected siblings must not crowd out
            # useful lower-ranked search hits or common authority sources.
            rows = original_frontier(bundle, limit=100000)
            return [{**row, 'issuer_route': url_scope(row['url'], profile)} for row in rows
                    if url_scope(row['url'], profile) != 'other_issuer'][:limit]

        def inventory(archive):
            result = original_inventory(archive)
            with zipfile.ZipFile(archive) as source:
                profiles = {name.split('/')[-2]: issuer_profile(json.loads(source.read(name))['request'])
                    for name in source.namelist() if name.endswith('/bundle.json')}
            excluded = [r for r in result['failed_fetches'] if
                url_scope(r['url'], profiles[r['task_id']]) == 'other_issuer']
            result['failed_fetches'] = [r for r in result['failed_fetches'] if r not in excluded]
            result['financial_excluded_routes'] = excluded
            result['financial_routing_policy'] = 'financial-acquisition-p0-v1'
            result['financial_original_inventory_counts'] = {key: result[key] for key in
                ('unique_failed_task_url_pairs', 'repair_candidate_task_url_pairs', 'failure_categories', 'failed_domains', 'proposed_routes')}
            rows = result['failed_fetches']
            result.update(unique_failed_task_url_pairs=sum(r['kind'] == 'failed_fetch' for r in rows),
                repair_candidate_task_url_pairs=len(rows),
                failure_categories=dict(Counter(r['category'] for r in rows)),
                failed_domains=dict(Counter(urlsplit(r['url']).hostname for r in rows)),
                proposed_routes=dict(Counter(r['proposed_route'] for r in rows)))
            return result

        def parse(page):
            result = original_parse(page)
            result['body_diagnostics'] = page_diagnostics(result, page)
            if result['body_diagnostics'].get('prior_rejection') and result.get('documents'):
                result['documents'][0].setdefault('metadata', {})['financial_same_version_rejection'] = {
                    'raw_sha256': result.get('sha256'),
                    'content_sha256': result['body_diagnostics']['content_sha256'],
                    'diagnostic': copy.deepcopy(result['body_diagnostics'])}
            if 'capture_status' in page:
                result['parent_capture_status'] = copy.deepcopy(page['capture_status'])
            return result

        def system(task, skills):
            return original_system(task, skills) + instruction(issuer_profile(task.bundle['request']))

        def view(task):
            result = original_view(task)
            result['financial_acquisition'] = research_contract(issuer_profile(task.bundle['request']))
            return result

        def catalog(task):
            profile = issuer_profile(task.bundle['request'])
            return {key: row for key, row in original_catalog(task).items()
                    if url_scope(row.get('url', key), profile) != 'other_issuer'}

        def page_view(task, page, start=0):
            check = page_scope(page.get('url', ''), page, issuer_profile(task.bundle['request']))
            diagnostic = page_diagnostics(page)
            if not check['eligible_for_target'] or not diagnostic['usable_text']:
                return {'url': page.get('url'), 'content': '', 'blocked': True,
                        'saved_chars': len(page.get('content', '')), 'body_diagnostics': diagnostic,
                        'financial_source_scope': check,
                        'warning': 'Original capture retained for audit. Not target financial evidence.'}
            return original_page_view(task, page, start)

        def replace_function(original, replacement):
            # Patch already-imported aliases as well as the defining module.
            # Later imports see the patched definition. All aliases are restored.
            for module in list(sys.modules.values()):
                if (module and getattr(module, '__name__', '').startswith('ForecastAgent.') and
                        not module.__name__.startswith('ForecastAgent.market_pulse')):
                    for name, value in list(vars(module).items()):
                        if value is original:
                            setattr(module, name, replacement)
            replacements.append((original, replacement))

        for original, replacement in ((BASE_DIAGNOSTICS, checked_diagnostics),
                (original_frontier, frontier), (original_inventory, inventory),
                (original_parse, parse), (stage.analysis_overlay, overlay),
                (original_system, system), (original_view, view)):
            # Keep the adapter's own captured baseline callable unchanged.
            replace_function(original, replacement)
        retrieval.RetrievalTask.catalog = catalog
        retrieval.RetrievalTask.page_view = page_view
        try:
            yield
        finally:
            retrieval.RetrievalTask.catalog = original_catalog
            retrieval.RetrievalTask.page_view = original_page_view
    finally:
        for original, replacement in reversed(replacements):
            for module in list(sys.modules.values()):
                if (module and getattr(module, '__name__', '').startswith('ForecastAgent.') and
                        not module.__name__.startswith('ForecastAgent.market_pulse')):
                    for name, value in list(vars(module).items()):
                        if value is replacement:
                            setattr(module, name, original)
        ACTIVE.release()


def collect(request, root):
    from ForecastAgent.releases.v1_0_5 import collect as release_collect
    prepared = prepare(request)
    with acquisition_policy():
        return release_collect(prepared, root)
