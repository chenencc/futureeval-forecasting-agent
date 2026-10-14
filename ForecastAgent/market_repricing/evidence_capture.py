"""Bounded eager capture and immutable evidence packets for new market tasks."""
import copy
import hashlib
import re
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .common import digest, now, read, write
from .materials import market_host, quality, SOCIAL_HOSTS

VERSION = 'market-evidence-capture-v2'
HTTP_CAP, BROWSER_CAP = 4, 2


def policy_identity():
    files = ('evidence_capture.py', 'materials.py', 'source_profile.py', 'acquisition_contract.py')
    return {'version': VERSION, 'initial_capture_budget': 'existing_native_fetch_attempts',
            'supplement_http_cap': HTTP_CAP, 'supplement_browser_cap': BROWSER_CAP,
            'tavily_basic_cap': 3, 'exa_cap': 1,
            'adapter_sha256_lf': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()
                .replace(b'\r\n', b'\n')).hexdigest() for name in files}}


def allowed_url(url):
    # DNS/public-address validation remains in the native network transport.
    try:
        from .acquisition_contract import action_link
        if action_link(url):
            return False
        part = urlsplit(url)
        return (part.scheme in {'http', 'https'} and bool(part.hostname) and
                not part.username and not part.password and not market_host(url))
    except ValueError:
        return False


def comparison_key(url):
    """Scope lookup only. Never rewrite a capture URL or its query parameters."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip('/') or '/', parts.query, ''))


def candidates(bundle):
    """Roles are navigation hints; they never certify relevance or truth."""
    request = bundle.get('request', {})
    named = set(request.get('external_rule_sources', []))
    for field in ('resolution_criteria', 'fine_print'):
        named.update(re.findall(r'https?://[^\s<>"\)]+', request.get(field, '')))
    terms = set(re.findall(r'\b[a-z0-9]{3,}\b', request.get('question', '').lower())) - {
        'will', 'the', 'before', 'after', '2026', '2027', 'with', 'any', 'first'}
    sources = {}
    def add(item, role=None, need_ids=(), query=None):
        if not isinstance(item, dict):
            return
        url = item.get('url', '')
        if not allowed_url(url):
            return
        host = urlsplit(url).hostname.lower()
        official = bool(re.search(r'(?:^|\.)gov(?:\.[a-z]{2})?$', host) or host.startswith('government.'))
        match = len(terms & set(re.findall(r'\b[a-z0-9]{3,}\b', (item.get('title', '') + ' ' + url).lower())))
        social = any(host == h or host.endswith('.' + h) for h in SOCIAL_HOSTS)
        roles = {'rules_designated'} if url in named else set()
        if official:
            roles.add('official_domain_hint')
        if role:
            roles.add('search_' + role)
        # Authority without a literal target match is not a reason to occupy all slots.
        score = 100 * (url in named) + (8 if official and match else 1 if official else 0) + 3 * match - 12 * social
        if item.get('origin') == 'observed_document_detail':
            score += 15
            roles.add('observed_document_detail')
            if item.get('parent_url') in named:
                score += 25
                roles.add('rules_source_detail')
        row = sources.setdefault(url, {'url': url, 'title': item.get('title', ''), 'host': host,
            'roles': [], 'need_ids': [], 'search_queries': [], 'priority': score, 'publication_hint': item.get('published_date') or item.get('publishedDate'),
            'relevance_verified': False})
        if not row['title'] and item.get('title'):
            row['title'] = item['title']
        if query and query not in row['search_queries']:
            row['search_queries'].append(query)
        for field in ('origin', 'depth', 'parent_url', 'parent_source_sha256', 'parent_retrieved_at_utc', 'observed_xpath', 'observed_attribute',
                      'automatic_capture_eligible', 'automatic_capture_block_reason'):
            if field in item:
                row[field] = item[field]
        row['roles'] = sorted(set(row['roles']) | roles)
        row['need_ids'] = sorted(set(row['need_ids']) | set(need_ids) | set(item.get('need_ids', [])))
        row['priority'] = max(row['priority'], score)
    leads = bundle.get('source_leads', {})
    for item in leads.values() if isinstance(leads, dict) else leads:
        add(item)
    for batch in bundle.get('searches', []) + bundle.get('exa_searches', []):
        for item in batch.get('results', []):
            add(item, batch.get('search_role'), batch.get('need_ids', []), batch.get('query'))
    for url in named:
        add({'url': url})
    for _ in range(3):
        for row in sources.values():
            if row.get('origin') == 'page_link' and row.get('parent_url') in sources:
                row['depth'] = sources[row['parent_url']].get('depth', 0) + 1
                row['automatic_capture_eligible'] = row['depth'] <= 1
    from .source_profile import observed_details, applicability
    # Only one local link layer. Every child keeps the exact original destination.
    for url, page in bundle.get('pages', {}).items():
        keys = {comparison_key(value) for value in (url, page.get('url'), page.get('final_url')) if value}
        variants = [lead for lead in sources.values() if comparison_key(lead['url']) in keys]
        parent = copy.deepcopy(max(variants, key=lambda lead: (bool(lead['search_queries']), bool(lead['title'])), default={}))
        if variants:
            parent['search_queries'] = sorted({query for lead in variants for query in lead['search_queries']})
            parent['need_ids'] = sorted({need for lead in variants for need in lead['need_ids']})
        if parent.get('depth', 0) >= 1 or parent.get('origin') in {'page_link', 'observed_document_detail'}:
            continue
        scope = applicability(page, url, parent, request)
        for item in observed_details(page, url):
            if scope.get('automatic_followup_requires_scope_review'):
                item.update(automatic_capture_eligible=False, automatic_capture_block_reason='parent_source_scope_requires_review')
            add(item, need_ids=parent.get('need_ids', []))
    from .acquisition_contract import sheet_export
    for url, page in bundle.get('pages', {}).items():
        export = sheet_export(page.get('url') or url)
        if not export:
            continue
        add({'url':export, 'title':'CSV data export of saved spreadsheet',
             'origin':'observed_data_export', 'parent_url':url,
             'parent_source_sha256':page.get('sha256'),
             'parent_retrieved_at_utc':page.get('retrieved_at_utc')})
        sources[export]['priority'] = 75
        sources[export]['roles'] = sorted(set(sources[export]['roles']) | {'data_export'})
    # Spread ordinary slots across publishers; designated originals remain first.
    ranked = sorted(sources.values(), key=lambda row: (-row['priority'], row['url']))
    primary, first, rest, hosts = [], [], [], set()
    for row in ranked:
        if 'rules_designated' in row['roles']:
            primary.append(row)
        elif row['host'] not in hosts:
            first.append(row); hosts.add(row['host'])
        else:
            rest.append(row)
    return primary + first + rest


def select_candidates(leads, *, limit, previous=()):
    """Distribute finite reads across needs and publishers without certifying them."""
    if not 0 <= limit <= 8:
        raise ValueError('Selection must fit the existing eight native slots')
    chosen, remaining = [], list(leads)
    used_hosts = {r.get('host') or urlsplit(r.get('url', '')).hostname for r in previous}
    used_needs = {n for r in previous for n in r.get('need_ids', [])}
    while remaining and len(chosen) < limit:
        def rank(row):
            mandatory = 'rules_designated' in row['roles']
            fresh_need = bool(set(row.get('need_ids', [])) - used_needs)
            return (not mandatory, not fresh_need, row['host'] in used_hosts, -row['priority'], row['url'])
        row = min(remaining, key=rank)
        remaining.remove(row); chosen.append(row)
        used_hosts.add(row['host']); used_needs.update(row.get('need_ids', []))
    return chosen


def packet(bundle, directory, reason):
    """First packet is write-once; later material has a separate immutable manifest."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    request = {k: bundle.get('request', {}).get(k) for k in (
        'id', 'question', 'question_type', 'resolution_criteria', 'fine_print', 'open_time',
        'as_of_utc', 'market_research_key', 'external_rule_sources')}
    objects = {}
    def keep(value):
        sha = digest(value)
        path = root / 'objects' / (sha + '.json')
        if path.exists() and digest(read(path)) != sha:
            raise ValueError('Evidence object changed')
        if not path.exists():
            write(path, value)
        return {'sha256': sha, 'path': 'objects/' + sha + '.json'}
    for url, page in bundle.get('pages', {}).items():
        # Diagnostic changes must not copy identical original HTML again.
        snapshot = {k: v for k, v in page.items() if k != 'body_diagnostics'}
        objects[url] = keep(snapshot) | {'captured_at': page.get('retrieved_at_utc'),
            'raw_sha256': page.get('sha256'), 'body_state': quality(page, url)['state']}
    searches = [keep(row) for row in bundle.get('searches', []) + bundle.get('exa_searches', [])]
    failed = [keep(row) for row in bundle.get('failed_captures', [])]
    capture_rows = keep(bundle.get('fetch_attempts', []))
    identity = digest({'request': request, 'pages': objects, 'searches': searches,
                       'failed_captures': failed, 'capture_attempts': capture_rows})
    first = root / 'first-packet.json'
    previous = [first] if first.exists() else []
    previous.extend(sorted((root / 'supplements').glob('*.json')) if (root / 'supplements').exists() else [])
    if any(read(p).get('material_sha256') == identity for p in previous):
        return read(next(p for p in previous if read(p).get('material_sha256') == identity))
    manifest = {'schema': VERSION, 'frozen_at': now(), 'reason': reason, 'request': request,
        'pages': objects, 'searches': searches, 'failed_captures': failed,
        'capture_attempts': capture_rows, 'material_sha256': identity,
        'first_packet': not first.exists(), 'semantic_completeness_verified': False,
        'source_packet_cutoff_is_not_market_creation': True}
    if first.exists():
        manifest['first_packet_sha256'] = digest(read(first))
        old = read(previous[-1])
        manifest['changed_page_urls'] = sorted(url for url in objects if old['pages'].get(url) != objects[url])
        manifest['raw_changed_page_urls'] = sorted(url for url in objects if
            old['pages'].get(url, {}).get('raw_sha256') != objects[url]['raw_sha256'])
        path = root / 'supplements' / ('%04d.json' % (len(previous)))
    else:
        path = first
    write(path, manifest)
    return manifest


def verify_packet(path):
    """Offline replay receipt validation without parsing or refetching a website."""
    import base64
    path = Path(path)
    root = path.parent.parent if path.parent.name == 'supplements' else path.parent
    manifest = read(path)
    expected = digest({k: manifest[k] for k in ('request', 'pages', 'searches', 'failed_captures', 'capture_attempts')})
    if expected != manifest['material_sha256']:
        raise ValueError('Frozen packet manifest changed')
    if manifest.get('first_packet_sha256') and digest(read(root / 'first-packet.json')) != manifest['first_packet_sha256']:
        raise ValueError('Initial packet lineage changed')
    refs = list(manifest['pages'].values()) + manifest['searches'] + manifest['failed_captures'] + [manifest['capture_attempts']]
    for ref in refs:
        target = (root / ref['path']).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file() or digest(read(target)) != ref['sha256']:
            raise ValueError('Frozen packet object checksum mismatch')
    for ref in manifest['pages'].values():
        page = read(root / ref['path'])
        if page.get('raw_response_base64') and hashlib.sha256(base64.b64decode(
                page['raw_response_base64'], validate=True)).hexdigest() != page.get('sha256'):
            raise ValueError('Original capture byte checksum mismatch')
        if page.get('retrieved_at_utc'):
            from .common import instant
            if instant(page['retrieved_at_utc']) > instant(manifest['frozen_at']):
                raise ValueError('Post-freeze capture in immutable packet')
    missing = [url for url, ref in manifest['pages'].items() if not read(root / ref['path']).get('raw_response_base64')]
    return {'objects_verified': len(refs), 'raw_bytes_unchanged': not missing, 'pages_without_original_bytes': missing, 'network_calls': 0}


def checklist(bundle, leads, records=()):
    from .source_profile import applicability
    pages = bundle.get('pages', {})
    attempted = {r.get('url') for r in bundle.get('fetch_attempts', [])} | {r.get('url') for r in records}
    rows = []
    for lead in leads:
        from .acquisition_contract import saved_source
        page, reuse = saved_source(bundle, lead['url'])
        diagnostic = quality(page, lead['url']) if page else None
        scope = applicability(page, lead['url'], lead, bundle.get('request', {})) if page else None
        rows.append(lead | {'capture_status': 'body_saved' if diagnostic and diagnostic['usable_text'] and diagnostic.get('qualifies_full_body', True) else
            'contextual_lead_saved' if diagnostic and diagnostic['usable_text'] else
            'lead_or_blocked_saved' if page else 'attempted_unavailable' if lead['url'] in attempted else 'unattempted',
            'body_state': diagnostic['state'] if diagnostic else None,
            'source_applicability': scope, 'target_coverage_verified': False,
            'body_role': diagnostic.get('body_role') if diagnostic else None,
            'data_coverage': diagnostic.get('data_coverage') if diagnostic else None,
            'saved_source_binding': reuse})
    missing = [r['url'] for r in rows if 'rules_designated' in r['roles'] and r['capture_status'] != 'body_saved']
    role_rows = {role: {'available_urls': len(selected := [r for r in rows if role in r['roles']]),
                       'attempted_urls': sum(r['capture_status'] != 'unattempted' for r in selected),
                       'saved_body_urls': sum(r['capture_status'] == 'body_saved' for r in selected)}
                 for role in ('rules_designated', 'official_domain_hint', 'search_primary', 'search_recent', 'search_gap')}
    need_rows = [{'id': need.get('id'), 'condition': need.get('condition'),
                 'expected_source': need.get('expected_source'), 'priority': need.get('priority'),
                 'associated_candidate_urls': [r['url'] for r in rows if need.get('id') in r['need_ids']],
                 'associated_saved_body_urls': [r['url'] for r in rows if need.get('id') in r['need_ids'] and r['capture_status'] == 'body_saved'],
                 'semantic_adequacy_verified': False} for need in bundle.get('plan') or []]
    return {'sources': rows, 'rules_designated_gaps': missing,
            'role_coverage': role_rows, 'planned_needs': need_rows,
            'unattempted_sources': [r['url'] for r in rows if r['capture_status'] == 'unattempted'],
            'source_scope_review_urls': [r['url'] for r in rows if (r.get('source_applicability') or {}).get('scope_review_required')],
            'readability_and_roles_are_not_truth': True, 'complete_recall_claimed': False}


def eager(task):
    """Use the real native private fetch implementation and its existing eight slots."""
    if task.cutoff or task.bundle.get('mode') != 'live' or task.bundle.get('result') or task.bundle.get('plan') is None:
        return
    from ForecastAgent.tavily_research import canonical_url
    selected = []
    event = {'at': now(), 'urls': [], 'outcomes': [],
             'native_fetch_cap_unchanged': True, 'model_requests': 0, 'search_requests': 0}
    task.bundle.setdefault('market_capture', {}).setdefault('eager_events', []).append(event)
    task.save()
    while len(selected) < 4 and task.budget()['page_fetch_remaining'] > 0:
        leads = candidates(task.bundle)
        for lead in leads:
            if 'rules_designated' in lead['roles'] or lead.get('origin') == 'observed_document_detail':
                origin = 'question_resolution_metadata' if 'rules_designated' in lead['roles'] else 'observed_document_detail'
                saved = task.bundle.setdefault('source_leads', {}).setdefault(canonical_url(lead['url']), {
                    **lead, 'origin': origin, 'source_request_sha256': digest(task.bundle['request'])})
                if origin == 'observed_document_detail':
                    binding = {k: lead[k] for k in ('parent_url', 'parent_source_sha256',
                        'parent_retrieved_at_utc', 'observed_xpath', 'observed_attribute')}
                    history = saved.setdefault('observed_document_bindings', [])
                    if binding not in history:
                        history.append(binding)
                    # Native page_link identity remains; stronger literal provenance is additive.
                    saved.update(binding, depth=1, automatic_capture_origin=origin)
        attempted = {canonical_url(r.get('url', '')) for r in task.bundle.get('fetch_attempts', [])}
        attempted.update(canonical_url(row['url']) for row in selected)
        from .acquisition_contract import saved_source
        available = [r for r in leads if r.get('origin') != 'observed_data_export' and r.get('automatic_capture_eligible', True) and canonical_url(r['url']) not in attempted and
                     saved_source(task.bundle, r['url'])[0] is None]
        choice = select_candidates(available, limit=1, previous=selected)
        if not choice:
            break
        row = choice[0]; selected.append(row); event['urls'].append(row['url'])
        try:
            # This private handler still validates catalog URLs, reserves before
            # HTTP, preserves byte/parser failures and invokes the public-address guard.
            task._execute('fetch_page', {'url': row['url']}, None)
            event['outcomes'].append({'url': row['url'], 'status': 'completed'})
        except Exception as exc:
            event['outcomes'].append({'url': row['url'], 'status': 'failed', 'error_type': type(exc).__name__})
        finally:
            task.save()  # Native failures and preflight errors both remain visible.
    task.bundle.setdefault('market_capture', {})['checklist'] = checklist(task.bundle, candidates(task.bundle))
    if event['outcomes']:
        task.bundle.setdefault('control', {})['read_after_discovery'] = len(task.bundle.get('searches', [])) + len(task.bundle.get('exa_searches', []))
        event['read_gate_scope'] = 'capture_attempts_only_not_model_comprehension'
    task.save()
    packet(task.bundle, task.directory / 'market-intake', 'after_search_eager_capture')


@contextmanager
def capture_adapter():
    """Scope the sidecar seam to requests with an exact declared capture policy."""
    from ForecastAgent.runtime import retrieval
    from unittest.mock import patch
    Task = retrieval.RetrievalTask
    original = Task._execute
    original_validate = retrieval.validate
    def validate(task, name, args, tools=None):
        if task.bundle.get('request', {}).get('market_acquisition_policy') == VERSION and tools and isinstance(args, dict):
            offered = {t['function']['name']: t for t in tools}
            if name == 'fetch_page' and 'read_sources' in offered:
                # Compatibility only when the equivalent reading capability is offered.
                # Exact catalog URLs, native budgets and public-host validation remain.
                from ForecastAgent.tools.capabilities import get
                tools = tools + [copy.deepcopy(get('fetch_page').definition)]
            if name == 'inspect_research_state' and name in offered and isinstance(args.get('limit'), int) and not isinstance(args['limit'], bool):
                schema = offered[name]['function']['parameters'].get('properties', {}).get('limit', {})
                lo, hi = schema.get('minimum', 1), schema.get('maximum', 8)
                effective = max(lo, min(hi, args['limit']))
                if effective != args['limit']:
                    task.bundle.setdefault('market_capture', {}).setdefault('parameter_normalizations', []).append({
                        'tool': name, 'field': 'limit', 'requested': args['limit'], 'effective': effective,
                        'at': now(), 'network_calls': 0})
                    args['limit'] = effective
        return original_validate(task, name, args, tools=tools)
    def wrapped(task, name, args, key):
        eager_search = task.bundle.get('request', {}).get('market_acquisition_policy') == VERSION and name in {'search_tavily', 'search_exa'}
        try:
            value = original(task, name, args, key)
        except Exception:
            if eager_search:
                eager(task)  # Save failed searches and capture already-known rule links.
            raise
        normalizations = task.bundle.get('market_capture', {}).get('parameter_normalizations', [])
        if isinstance(value, dict) and normalizations and normalizations[-1]['tool'] == name:
            value['parameter_normalization'] = normalizations[-1]
        if eager_search:
            eager(task)
        return value
    from ForecastAgent.intelligence import admission
    original_inspect = admission.inspect
    def inspect(page):
        return quality(page, page.get('url') or page.get('final_url') or '', inspector=original_inspect)
    with patch.object(Task, '_execute', wrapped), patch.object(retrieval, 'validate', validate), patch.object(admission, 'inspect', inspect):
        yield


def recover(bundle, directory, capture=None):
    """Attempt important saved leads within unchanged 4-HTTP/2-browser repair caps."""
    if capture is None:
        from ForecastAgent.supplement.transport import capture as capture
    root = Path(directory).resolve()
    identity = policy_identity() | {'parent_sha256': digest(bundle)}
    if (root / 'identity.json').exists() and read(root / 'identity.json') != identity:
        raise ValueError('Frozen evidence supplement policy changed')
    write(root / 'identity.json', identity)
    if (root / 'result.json').exists():
        report, child = read(root / 'result.json'), read(root / 'bundle.json')
        if digest(child) != report['bundle_sha256']:
            raise ValueError('Evidence supplement bytes changed')
        return child, report
    rows = read(root / 'captures.json') if (root / 'captures.json').exists() else []
    child = copy.deepcopy(bundle)
    for row in rows:
        if row.get('page_file'):
            child.setdefault('pages', {})[row['url']] = read(root / row['page_file'])
    leads = candidates(bundle)
    write(root / 'saved-leads.json', leads)
    # Keep the native pre-supplement snapshot separate from this later stage.
    packet(bundle, root / 'packets', 'pre_independent_supplement')
    for method, cap in (('http', HTTP_CAP), ('browser', BROWSER_CAP)):
        while sum(r['method'] == method for r in rows) < cap:
            leads = candidates(child)
            available = []
            for lead in leads:
                if not lead.get('automatic_capture_eligible', True):
                    continue
                url = lead['url']
                from .acquisition_contract import saved_source
                page, binding = saved_source(child, url)
                if page:
                    diagnostic = quality(page, url)
                    if diagnostic['usable_text'] and (method == 'http' or diagnostic.get('qualifies_full_body', True)):
                        if binding['source_url'] != url:
                            reused = child.setdefault('market_source_reuse', [])
                            receipt = {'requested_url':url, **binding, 'new_http_requests':0,
                                       'source_clock_preserved':True}
                            if receipt not in reused:
                                reused.append(receipt)
                        continue
                    if diagnostic.get('source_profile', {}).get('role') == 'document_catalog':
                        continue
                if any(r['url'] == url and r['method'] == method for r in rows):
                    continue
                if method == 'http' and any(r.get('url') == url for r in bundle.get('fetch_attempts', [])):
                    continue
                if method == 'browser':
                    previous = [r for r in rows if r['url'] == url and r['method'] == 'http']
                    native = [r for r in bundle.get('fetch_attempts', []) if r.get('url') == url]
                    if not previous and not native:
                        continue
                    if urlsplit(url).path.lower().endswith(('.pdf', '.csv', '.json', '.zip', '.xlsx')):
                        continue
                available.append(lead)
            selection = select_candidates(available, limit=1, previous=rows)
            if not selection:
                break
            lead = selection[0]
            url = lead['url']
            index = len(rows) + 1
            row = {'method': method, 'url': url, 'host': lead['host'], 'need_ids': lead['need_ids'],
                   'roles': lead['roles'], 'reserved_at': now(), 'status': 'reserved_unknown'}
            if lead.get('origin') == 'observed_document_detail':
                row['observed_detail_provenance'] = {k: lead[k] for k in (
                    'parent_url', 'parent_source_sha256', 'parent_retrieved_at_utc', 'observed_xpath', 'observed_attribute')}
            rows.append(row); write(root / 'captures.json', rows)
            try:
                page = capture(method, url, root / ('capture-' + str(index)), timeout_seconds=60,
                    browser_profile={'backend': 'crawl4ai', 'query': ''} if method == 'browser' else None)
                page.setdefault('retrieved_at_utc', now())
                from .acquisition_contract import materialize_csv
                page = materialize_csv(page, url)
                filename = 'page-' + str(index) + '.json'
                write(root / filename, page)
                child.setdefault('pages', {})[url] = page
                row.update(status='completed', page_file=filename, diagnostic=quality(page, url),
                           captured_at=page['retrieved_at_utc'], body_sha256=digest(page.get('content', '')))
            except Exception as exc:
                row.update(status='failed', error_type=type(exc).__name__)
            finally:
                write(root / 'captures.json', rows)
    status = checklist(child, candidates(child), rows)
    child.setdefault('gaps', []).extend({'code': 'important_source_unavailable', 'url': url} for url in status['rules_designated_gaps'])
    child['market_capture'] = {'version': VERSION, 'checklist': status, 'supplemented_at': now(), 'parent_sha256': digest(bundle)}
    packet(child, root / 'packets', 'after_independent_supplement')
    from .materials import prepare
    view, audit = prepare(child)
    report = {'version': VERSION, 'external_body_count': len(view['pages']), 'admission': audit,
        'external_full_body_count': audit.get('external_full_body_count', len(view['pages'])),
        'physical_capture_reservations': len(rows), 'new_searches': 0, 'budget_reset': False,
        'checklist': status, 'parent_sha256': digest(bundle), 'bundle_sha256': digest(child)}
    report['saved_source_reuse'] = child.get('market_source_reuse', [])
    report['data_coverage'] = {url:quality(page, url).get('data_coverage') for url,page in child.get('pages',{}).items()
        if quality(page, url).get('data_coverage',{}).get('table_document_count') or page.get('data_export_coverage')}
    write(root / 'bundle.json', child); write(root / 'result.json', report)
    return child, report
