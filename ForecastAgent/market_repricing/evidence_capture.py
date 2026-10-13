"""Bounded eager capture and immutable evidence packets for new market tasks."""
import copy
import hashlib
import re
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .common import digest, now, read, write
from .materials import market_host, quality, SOCIAL_HOSTS

VERSION = 'market-evidence-capture-v2'
HTTP_CAP, BROWSER_CAP = 4, 2


def policy_identity():
    files = ('evidence_capture.py', 'materials.py')
    return {'version': VERSION, 'initial_capture_budget': 'existing_native_fetch_attempts',
            'supplement_http_cap': HTTP_CAP, 'supplement_browser_cap': BROWSER_CAP,
            'tavily_basic_cap': 3, 'exa_cap': 1,
            'adapter_sha256_lf': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()
                .replace(b'\r\n', b'\n')).hexdigest() for name in files}}


def allowed_url(url):
    # DNS/public-address validation remains in the native network transport.
    try:
        part = urlsplit(url)
        return (part.scheme in {'http', 'https'} and bool(part.hostname) and
                not part.username and not part.password and not market_host(url))
    except ValueError:
        return False


def candidates(bundle):
    """Roles are navigation hints; they never certify relevance or truth."""
    request = bundle.get('request', {})
    named = set(request.get('external_rule_sources', []))
    for field in ('resolution_criteria', 'fine_print'):
        named.update(re.findall(r'https?://[^\s<>"\)]+', request.get(field, '')))
    terms = set(re.findall(r'\b[a-z0-9]{3,}\b', request.get('question', '').lower())) - {
        'will', 'the', 'before', 'after', '2026', '2027', 'with', 'any', 'first'}
    sources = {}
    def add(item, role=None, need_ids=()):
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
        score = 100 * (url in named) + 12 * official + 3 * match - 12 * social
        row = sources.setdefault(url, {'url': url, 'title': item.get('title', ''), 'host': host,
            'roles': [], 'need_ids': [], 'priority': score, 'publication_hint': item.get('published_date') or item.get('publishedDate'),
            'relevance_verified': False})
        row['roles'] = sorted(set(row['roles']) | roles)
        row['need_ids'] = sorted(set(row['need_ids']) | set(need_ids) | set(item.get('need_ids', [])))
        row['priority'] = max(row['priority'], score)
    leads = bundle.get('source_leads', {})
    for item in leads.values() if isinstance(leads, dict) else leads:
        add(item)
    for batch in bundle.get('searches', []) + bundle.get('exa_searches', []):
        for item in batch.get('results', []):
            add(item, batch.get('search_role'), batch.get('need_ids', []))
    for url in named:
        add({'url': url})
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
    pages = bundle.get('pages', {})
    attempted = {r.get('url') for r in bundle.get('fetch_attempts', [])} | {r.get('url') for r in records}
    rows = []
    for lead in leads:
        page = pages.get(lead['url'])
        if page is None:
            # Native keys are canonical; original URL spelling remains in the page.
            page = next((p for p in pages.values() if p.get('url') == lead['url']), None)
        diagnostic = quality(page, lead['url']) if page else None
        rows.append(lead | {'capture_status': 'body_saved' if diagnostic and diagnostic['usable_text'] and diagnostic.get('qualifies_full_body', True) else
            'contextual_lead_saved' if diagnostic and diagnostic['usable_text'] else
            'lead_or_blocked_saved' if page else 'attempted_unavailable' if lead['url'] in attempted else 'unattempted',
            'body_state': diagnostic['state'] if diagnostic else None})
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
            'readability_and_roles_are_not_truth': True, 'complete_recall_claimed': False}


def eager(task):
    """Use the real native private fetch implementation and its existing eight slots."""
    if task.cutoff or task.bundle.get('mode') != 'live' or task.bundle.get('result') or task.bundle.get('plan') is None:
        return
    from ForecastAgent.tavily_research import canonical_url
    leads = candidates(task.bundle)
    for lead in leads:
        if 'rules_designated' in lead['roles']:
            task.bundle.setdefault('source_leads', {}).setdefault(canonical_url(lead['url']), {
                'url': lead['url'], 'origin': 'question_resolution_metadata',
                'source_request_sha256': digest(task.bundle['request'])})
    attempted = {canonical_url(r.get('url', '')) for r in task.bundle.get('fetch_attempts', [])}
    selected = [r for r in leads if canonical_url(r['url']) not in attempted and
                canonical_url(r['url']) not in task.bundle.get('pages', {})]
    selected = selected[:min(4, task.budget()['page_fetch_remaining'])]
    event = {'at': now(), 'urls': [row['url'] for row in selected], 'outcomes': [],
             'native_fetch_cap_unchanged': True, 'model_requests': 0, 'search_requests': 0}
    task.bundle.setdefault('market_capture', {}).setdefault('eager_events', []).append(event)
    task.save()
    for row in selected:
        try:
            # This private handler still validates catalog URLs, reserves before
            # HTTP, preserves byte/parser failures and invokes the public-address guard.
            task._execute('fetch_page', {'url': row['url']}, None)
            event['outcomes'].append({'url': row['url'], 'status': 'completed'})
        except Exception as exc:
            event['outcomes'].append({'url': row['url'], 'status': 'failed', 'error_type': type(exc).__name__})
        finally:
            task.save()  # Native failures and preflight errors both remain visible.
    task.bundle.setdefault('market_capture', {})['checklist'] = checklist(task.bundle, leads)
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
    with patch.object(Task, '_execute', wrapped), patch.object(retrieval, 'validate', validate):
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
        for lead in leads:
            url = lead['url']
            page = child.get('pages', {}).get(url)
            if page and quality(page, url)['usable_text']:
                continue
            if any(r['url'] == url and r['method'] == method for r in rows):
                continue
            if sum(r['method'] == method for r in rows) >= cap:
                break
            if method == 'http' and any(r.get('url') == url for r in bundle.get('fetch_attempts', [])):
                continue  # An initial HTTP attempt cannot be renewed as supplement HTTP.
            if method == 'browser':
                previous = [r for r in rows if r['url'] == url and r['method'] == 'http']
                native = [r for r in bundle.get('fetch_attempts', []) if r.get('url') == url]
                if not previous and not native:
                    continue
                if urlsplit(url).path.lower().endswith(('.pdf', '.csv', '.json', '.zip', '.xlsx')):
                    continue
            index = len(rows) + 1
            row = {'method': method, 'url': url, 'roles': lead['roles'], 'reserved_at': now(), 'status': 'reserved_unknown'}
            rows.append(row); write(root / 'captures.json', rows)
            try:
                page = capture(method, url, root / ('capture-' + str(index)), timeout_seconds=60,
                    browser_profile={'backend': 'crawl4ai', 'query': ''} if method == 'browser' else None)
                page.setdefault('retrieved_at_utc', now())
                filename = 'page-' + str(index) + '.json'
                write(root / filename, page)
                child.setdefault('pages', {})[url] = page
                row.update(status='completed', page_file=filename, diagnostic=quality(page, url),
                           captured_at=page['retrieved_at_utc'], body_sha256=digest(page.get('content', '')))
            except Exception as exc:
                row.update(status='failed', error_type=type(exc).__name__)
            finally:
                write(root / 'captures.json', rows)
    status = checklist(child, leads, rows)
    child.setdefault('gaps', []).extend({'code': 'important_source_unavailable', 'url': url} for url in status['rules_designated_gaps'])
    child['market_capture'] = {'version': VERSION, 'checklist': status, 'supplemented_at': now(), 'parent_sha256': digest(bundle)}
    packet(child, root / 'packets', 'after_independent_supplement')
    from .materials import prepare
    view, audit = prepare(child)
    report = {'version': VERSION, 'external_body_count': len(view['pages']), 'admission': audit,
        'external_full_body_count': audit.get('external_full_body_count', len(view['pages'])),
        'physical_capture_reservations': len(rows), 'new_searches': 0, 'budget_reset': False,
        'checklist': status, 'parent_sha256': digest(bundle), 'bundle_sha256': digest(child)}
    write(root / 'bundle.json', child); write(root / 'result.json', report)
    return child, report
