"""Native source admission and original-link provenance for discovery captures."""
from urllib.parse import urlsplit

from ForecastAgent.tavily_research import canonical_url
from ForecastAgent.tools.intelligence_box.discovery import ROUTES, parse_discovery
from ForecastAgent.tools.intelligence_box.profiles import PROFILES, resolve_profile


DISCOVERY_ROLES = {'news_discovery', 'official_announcement', 'publisher_discovery',
                   'issuer_discovery', 'official_document_discovery'}


def is_index(capture):
    return capture.get('source_id', '').startswith('discovery:') or (
        capture.get('role') in DISCOVERY_ROLES and
        not any(isinstance(r, dict) and 'page_content' in r
                for r in capture.get('records', [])))


def candidate(task, args, box):
    """Reparse the original parent before admission; native cache is not authority."""
    parent, raw = box._saved(args['capture_id'])
    recorded = task.bundle['channel_tools']['captures'][args['capture_id']]
    immutable = ('id', 'source_id', 'request_url', 'parameters', 'captured_at_utc',
                 'raw_sha256', 'status', 'records', 'http', 'source_binding')
    if any(parent.get(k) != recorded.get(k) for k in immutable):
        raise ValueError('Native discovery capture differs from saved original')
    if not parent['source_id'].startswith('discovery:') or parent['status'] not in {'usable', 'empty'}:
        raise ValueError('Requires a successful task-owned discovery capture')
    params = parent['parameters']
    profile = PROFILES.get(params.get('profile_id'))
    rows, _ = parse_discovery({'discovery_parameters':params,
        'final_url':parent['http']['final_url'],
        'response_headers':parent['http'].get('response_headers', {}),
        'discovery_hosts':profile['hosts'] if profile else [urlsplit(parent['request_url']).hostname]}, raw)
    index = args['index']
    if not 0 <= index < len(rows) or rows[index] != parent['records'][index]:
        raise ValueError('Candidate does not match saved raw index')
    selected = rows[index]
    if not selected.get('eligible'):
        raise ValueError('Candidate host or credential policy rejected')
    if selected.get('target_kind') == 'sitemap':
        raise ValueError('Child sitemap requires an explicit discover call')
    box._check_read_url(selected['url'])
    return selected


def preflight(task, name, args, box):
    if name == 'intelligence_acquire_link':
        candidate(task, args, box)
    elif name == 'intelligence_discover':
        url = args['url']
        known = canonical_url(url) in {**task.catalog(), **task.bundle['pages']}
        curated = any(canonical_url(r['url']) == canonical_url(url)
                      and r['kind'] == args['kind'] for r in ROUTES)
        if args.get('profile_id'):
            profile, _ = resolve_profile(args['profile_id'], url)
            curated |= canonical_url(url) == canonical_url(profile['url'])
        if not known and not curated:
            raise ValueError('Discovery requires a catalog route or exact previously discovered URL')
        if args['kind'] == 'ir' and not args.get('profile_id'):
            raise ValueError('Issuer discovery requires a curated profile_id')
        box._check_read_url(url)


def preserve_leads(task, capture):
    """Discovery rows remain leads, never a JSON surrogate for article bodies."""
    for index, row in enumerate(capture.get('records', [])):
        url = row.get('url') or row.get('link')
        if not isinstance(url, str) or not url:
            continue
        from ForecastAgent.retrieval_sources import allowed_source
        if not allowed_source(url) or row.get('eligible') is False:
            continue
        key = canonical_url(url)
        task.bundle['source_leads'].setdefault(key, {
            'url':url, 'title':row.get('label') or row.get('title'),
            'origin':'discovery_index', 'parent_url':capture['request_url'],
            'parent_capture_id':capture['id'], 'parent_raw_sha256':capture['raw_sha256'],
            'candidate_index':index, 'target_kind':row.get('target_kind', 'document'),
            'discovery_captured_at_utc':capture['captured_at_utc'],
            'source_id':capture['source_id'], 'source_role':capture['role'],
            'publisher_entity':row.get('publisher_entity'), 'cik':row.get('cik'),
            'publication_hint':row.get('pubDate') or row.get('published') or row.get('lastmod') or row.get('seendate'),
            'full_article_body':False, 'truth_verified':False})
