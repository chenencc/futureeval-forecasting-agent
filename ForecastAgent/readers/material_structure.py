"""Source-bound HTML roles, observed resources and tabular response projections.

These readers never fetch, infer an event outcome, or replace archived bytes.
Roles are markup observations, not source-reliability or relevance judgments.
"""
import base64
import csv
import hashlib
import io
import ipaddress
import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from ForecastAgent.readers.encoding import decode_response

VERSION = 'source_structure_v1'
MAX_BYTES = 8_000_000


def saved_bytes(snapshot):
    raw = base64.b64decode(snapshot['raw_response_base64'], validate=True)
    if hashlib.sha256(raw).hexdigest() != snapshot['sha256']:
        raise ValueError('Saved source hash mismatch')
    if len(raw) > MAX_BYTES:
        raise ValueError('Saved source exceeds structure-reader bounds')
    decoded, _ = decode_response(raw, snapshot.get('response_headers'))
    return decoded


def html_tree(snapshot):
    from lxml import html
    return html.fromstring(saved_bytes(snapshot).decode(snapshot.get('charset') or 'utf-8', errors='replace'))


def observable_url(value, parent):
    """Offline syntactic eligibility; execution still requires live public DNS checks."""
    url = urljoin(parent, str(value or '').strip())
    try:
        parts = urlsplit(url)
        _ = parts.port
        host = (parts.hostname or '').lower().rstrip('.')
        if parts.scheme not in {'http', 'https'} or not host or parts.username or parts.password:
            return None
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))
    except ValueError:
        return None


def discover_resources(snapshot, *, limit=20):
    if not 1 <= limit <= 50:
        raise ValueError('Resource limit must be one to fifty')
    tree = html_tree(snapshot)
    source = snapshot.get('final_url') or snapshot['url']
    base_nodes = tree.xpath('//base[@href]')
    base = observable_url(base_nodes[0].get('href'), source) if base_nodes else source
    base = base or source
    observations = []
    for node in tree.xpath('//iframe[@src] | //embed[@src] | //object[@data] | //link[@href]')[:2000]:
        tag = str(node.tag).lower()
        if tag == 'link' and not (set(node.get('rel', '').lower().split()) & {'alternate'}):
            continue
        attr = 'data' if tag == 'object' else 'href' if tag == 'link' else 'src'
        observations.append((node.get(attr), {'kind': 'embedded_document' if tag != 'link' else 'alternate_resource',
            'observation': 'html_attribute', 'xpath': tree.getroottree().getpath(node), 'attribute': attr,
            'title': node.get('title', '')[:400], 'declared_type': node.get('type'), 'dispatch_observed': None}))
    for index, row in enumerate(snapshot.get('browser_audit', {}).get('requests', [])[:500]):
        if row.get('method') not in {'GET', 'HEAD'} or row.get('resource_type') not in {'xhr', 'fetch'}:
            continue
        observations.append((row['url'], {'kind': 'observed_data_request', 'observation': 'browser_route_audit',
            'audit_index': index, 'dispatch_observed': row.get('allowed'), 'blocked_reason': row.get('reason')}))
    for index, row in enumerate(snapshot.get('data_response_capture', {}).get('records', [])):
        observations.append((row['url'], {'kind': 'observed_data_response', 'observation': 'browser_response_audit',
            'audit_index': index, 'dispatch_observed': True, 'response_status': row.get('http_status')}))
    grouped = {}
    for value, record in observations:
        url = observable_url(value, base)
        if not url:
            continue
        if url not in grouped:
            grouped[url] = {'url': url, 'observations': [], 'parent_url': source,
                'parent_source_sha256': snapshot['sha256'], 'parent_retrieved_at_utc': snapshot.get('retrieved_at_utc'),
                'same_origin': (urlsplit(url).scheme, urlsplit(url).netloc) == (urlsplit(source).scheme, urlsplit(source).netloc),
                'public_destination_verified': False, 'relevance_verified': False, 'automatic_fetch': False}
        grouped[url]['observations'].append(record)
    return {'schema': VERSION, 'resources': list(grouped.values())[:limit],
            'truncated': len(grouped) > limit, 'observed_unique_count': len(grouped), 'network_calls': 0}


def node_role(node):
    mapping = {'nav': 'navigation', 'footer': 'footer', 'header': 'header', 'aside': 'aside',
               'article': 'article', 'main': 'main'}
    aria = {'navigation': 'navigation', 'contentinfo': 'footer', 'banner': 'header',
            'complementary': 'aside', 'main': 'main', 'article': 'article', 'tabpanel': 'content_panel'}
    ancestry = [node, *node.iterancestors()]
    # Semantic content containers take precedence over a CSS token such as
    # "nav-tab1" on a data table panel. Tabs are not navigation-only evidence.
    for current in ancestry:
        declared = current.get('role', '').lower()
        if declared in aria:
            return aria[declared], 'aria_role'
        if str(current.tag).lower() in mapping:
            return mapping[str(current.tag).lower()], 'html_tag'
    for current in ancestry:
        tokens = set(re.split(r'[^a-z]+', (current.get('id', '')+' '+current.get('class', '')).lower()))
        if tokens & {'footer', 'copyright'}:
            return 'footer', 'markup_token_heuristic'
        if tokens & {'navigation', 'navbar', 'nav', 'menu', 'breadcrumbs', 'breadcrumb'}:
            return 'navigation', 'markup_token_heuristic'
    return 'unknown', 'no_explicit_role'


def source_sections(snapshot, *, max_chars=150000, max_sections=1000):
    if not 1 <= max_chars <= 150000 or not 1 <= max_sections <= 1000:
        raise ValueError('Section bounds exceed supported limits')
    tree = html_tree(snapshot)
    for node in tree.xpath('//script | //style | //noscript'):
        node.drop_tree()
    xpath = '//h1 | //h2 | //h3 | //h4 | //h5 | //h6 | //p | //li | //dt | //dd | //time | //table | //div | //span'
    sections, remaining, examined = [], max_chars, 0
    headings = []
    for node in tree.xpath(xpath)[:10000]:
        examined += 1
        tag = str(node.tag).lower()
        if any(str(a.tag).lower() == 'table' and not a.xpath('.//table') for a in node.iterancestors()):
            continue
        if tag == 'table' and node.xpath('.//table'):
            continue  # A nested layout table is not one combined evidence block.
        if tag in {'div', 'span', 'li'} and node.xpath('.//p | .//table | .//div | .//li | .//h1 | .//h2 | .//h3 | .//h4'):
            continue
        if tag == 'span' and any(str(a.tag).lower() in {'p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'} for a in node.iterancestors()):
            continue
        text = re.sub(r'\s+', ' ', node.text_content()).strip()
        if not text:
            continue
        role, reason = node_role(node)
        if role == 'unknown' and len(text) < 500 and re.match(r'^(?:©|copyright\s+(?:\(c\)\s*)?\d{4})', text, re.I):
            role, reason = 'copyright', 'explicit_copyright_prefix'
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'} and role not in {'navigation', 'footer', 'copyright', 'header', 'aside'}:
            headings.append(text[:800])
        if remaining <= 0 or len(sections) >= max_sections:
            break
        clipped = text[:remaining]
        remaining -= len(clipped)
        sections.append({'text': clipped, 'role': role, 'role_basis': reason, 'tag': tag,
            'xpath': tree.getroottree().getpath(node), 'source_sha256': snapshot['sha256'],
            'projection_text_sha256': hashlib.sha256(clipped.encode()).hexdigest(),
            'truncated': len(clipped) < len(text), 'heading_context': headings[-3:],
            'explicit_datetime_attribute': node.get('datetime') if tag == 'time' else None})
    excluded = {'navigation', 'footer', 'copyright', 'header', 'aside'}
    return {'schema': VERSION, 'sections': sections, 'source_sha256': snapshot['sha256'],
            'explicit_date_metadata': [{'xpath': tree.getroottree().getpath(n),
                'property': n.get('itemprop') or n.get('property') or n.get('name'),
                'value': n.get('content') or n.get('datetime') or re.sub(r'\s+', ' ', n.text_content()).strip(),
                'source_sha256': snapshot['sha256'], 'semantics_verified': False}
                for n in tree.xpath('//*[@itemprop="datePublished" or @itemprop="dateModified" or @property="article:published_time" or @property="article:modified_time"]')[:20]],
            'unknown_roles_are_not_article_certification': True,
            'reading_projection': '\n'.join(s['text'] for s in sections if s['role'] not in excluded),
            'excluded_roles_remain_archived': sorted(excluded), 'network_calls': 0,
            'truncated': remaining <= 0 or len(sections) >= max_sections or examined >= 10000}


def structured_rows(snapshot, *, max_rows=2000, max_chars=150000):
    if not 1 <= max_rows <= 2000 or not 1 <= max_chars <= 150000:
        raise ValueError('Structured row bounds exceed supported limits')
    decoded = saved_bytes(snapshot)
    text = decoded.decode(snapshot.get('charset') or 'utf-8', errors='replace')
    kind = snapshot.get('content_type', '')
    source = snapshot.get('final_url') or snapshot['url']
    result = {'schema': VERSION, 'source_url': source, 'source_sha256': snapshot['sha256'],
              'retrieved_at_utc': snapshot.get('retrieved_at_utc'),
              'request_parameters': parse_qs(urlsplit(source).query, keep_blank_values=True),
              'reported_metadata': {}, 'tables': [], 'network_calls': 0, 'truth_verified': False,
              'parameter_values_are_requested_not_confirmed': True}
    code = snapshot.get('http_status', snapshot.get('navigation_http_status'))
    if isinstance(code, int) and code >= 400:
        result.update(state='http_error', http_status=code, row_count=0)
        return result
    tables = []
    if kind in {'text/csv', 'application/csv'}:
        reader = csv.reader(io.StringIO(text))
        original_headers = next(reader, [])
        headers = original_headers[:40]
        result['columns_truncated'] = len(original_headers) > 40
        records = []
        for row in reader:
            records.append(row[:40])
            if len(records) > max_rows:
                break
        tables.append(('$', headers, records))
        result['duplicate_header_names'] = len(headers) != len(set(headers))
    elif kind == 'application/json' or text.lstrip().startswith(('{', '[')):
        value = json.loads(text)
        if isinstance(value, dict) and value.get('error'):
            result.update(state='remote_error', remote_error=str(value['error'])[:2000])
            return result
        nodes = 0
        def visit(item, path='$', depth=0):
            nonlocal nodes
            nodes += 1
            if nodes > 10000 or depth > 8 or len(tables) >= 10:
                result['structure_truncated'] = True
                return
            if isinstance(item, list) and item:
                sample = item[:max_rows+1]
                if all(isinstance(r, dict) for r in sample):
                    all_headers = list(dict.fromkeys(k for row in sample for k in row))
                    headers = all_headers[:40]
                    tables.append((path, headers, [[row.get(k) for k in headers] for row in sample]))
                    if len(all_headers) > 40: result['structure_truncated'] = True
                elif all(isinstance(r, list) for r in sample):
                    width = min(40, max(map(len, sample)))
                    tables.append((path, ['column_'+str(i) for i in range(width)], [r[:width] for r in sample]))
                elif all(not isinstance(r, (dict, list)) for r in sample):
                    tables.append((path, ['value'], [[r] for r in sample]))
                else:
                    result['mixed_collection_not_normalized'] = path
            elif isinstance(item, dict):
                for key, child in list(item.items())[:100]:
                    if key.lower() in {'metadata', 'meta', 'units', 'datum', 'station', 'timezone', 'time_zone', 'product'}:
                        result['reported_metadata'][path+'.'+key] = child
                    visit(child, path+'.'+key, depth+1)
        visit(value)
        encoded = json.dumps(result['reported_metadata'], ensure_ascii=False)
        if len(encoded) > 16000:
            result['reported_metadata'] = {'bounded_json_preview': encoded[:16000]}
            result['metadata_truncated'] = True
    else:
        raise ValueError('Expected saved JSON or CSV data')
    remaining, count = max_chars, 0
    for path, headers, records in tables:
        rows = []
        for index, row in enumerate(records):
            serialized = json.dumps(row, ensure_ascii=False)
            if count >= max_rows or len(serialized) > remaining:
                break
            remaining -= len(serialized)
            count += 1
            rows.append({'source_row_index': index, 'cells': row})
        result['tables'].append({'path': path, 'columns': headers, 'rows': rows,
            'truncated': len(rows) != len(records), 'record_semantics_verified': False})
    result.update(state='rows_available' if count else 'no_row_collection', row_count=count,
                  rows_truncated=any(t['truncated'] for t in result['tables']))
    return result
