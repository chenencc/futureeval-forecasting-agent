"""Offline source roles adapted from market-v0.3.4; never certify relevance."""
import copy
import re
import unicodedata
from functools import lru_cache
from urllib.parse import urlsplit

VERSION = 'acquisition-source-profile-v1'
CHROME = {'navigation', 'footer', 'copyright', 'header', 'aside'}
CONTENT = {'article', 'main', 'content_panel'}
DOCUMENT_SUFFIXES = ('.pdf', '.csv', '.json', '.xml', '.xlsx', '.xls')
UNSUPPORTED_DOCUMENT_SUFFIXES = ('.doc', '.docx', '.odt', '.rtf')


def visible(text):
    return ' '.join(''.join(char for char in str(text or '') if unicodedata.category(char) != 'Cf').split())


def tokens(text):
    return {word.casefold() for word in re.findall(r'[^\W\d_]{4,}', visible(text), re.UNICODE)}


@lru_cache(maxsize=16)
def _structure(raw64, sha, encoding, charset, url, kind):
    from ForecastAgent.readers.material_structure import source_sections
    snapshot = {'raw_response_base64': raw64, 'sha256': sha, 'response_headers': {'Content-Encoding': encoding} if encoding else {},
                'charset': charset, 'url': url, 'final_url': url, 'content_type': kind}
    return source_sections(snapshot, max_chars=150000, max_sections=1000)


def profile(page, url):
    """Reuse the native saved-byte reader. Unknown markup remains unknown."""
    kind = page.get('content_type', '').split(';', 1)[0]
    result = {'schema': VERSION, 'source_url': url, 'source_sha256': page.get('sha256'),
              'captured_at': page.get('retrieved_at_utc'), 'role': 'unclassified_original',
              'qualifies_full_body': None, 'semantic_adequacy_verified': False, 'network_calls': 0}
    if kind not in {'text/html', 'application/xhtml+xml'} or not page.get('raw_response_base64'):
        result['structure_status'] = 'not_available'
        return result
    try:
        structure = _structure(page['raw_response_base64'], page['sha256'],
            page.get('response_headers', {}).get('Content-Encoding'), page.get('charset', 'utf-8'),
            page.get('final_url') or url, kind)
    except Exception as exc:
        result.update(structure_status='unavailable', error_type=type(exc).__name__)
        return result
    sections = [section for section in structure['sections'] if visible(section.get('text'))]
    nonchrome = [section for section in sections if section['role'] not in CHROME]
    headings = [section for section in nonchrome if re.fullmatch(r'h[1-6]', section['tag'])]
    prose = [section for section in nonchrome if section['tag'] == 'p' and len(visible(section['text'])) >= 40]
    tables = [section for section in nonchrome if section['tag'] == 'table']
    dated_values = [section for section in nonchrome if not re.fullmatch(r'h[1-6]', section['tag'])
                    and re.search(r'\b20\d{2}-\d{2}-\d{2}\b', visible(section['text']))
                    and re.search(r'\d(?:[.,]\d+)?\s*(?:%|bps|USD|EUR|GBP)\b|\d(?:[.,]\d+)?\s*%', visible(section['text']))]
    result.update(structure_status='available', structure_truncated=structure.get('truncated', False),
        section_count=len(sections), nonchrome_section_count=len(nonchrome),
        paragraph_count=len(prose), table_count=len(tables),
        headings=[{k: section.get(k) for k in ('text', 'xpath', 'source_sha256', 'role')} for section in headings[:12]],
        explicit_dates=structure.get('explicit_date_metadata', []),
        unknown_markup_is_not_article_certification=True)
    header_value = bool(re.search(r'\b20\d{2}-\d{2}-\d{2}\b', visible(page.get('content'))) and
                        re.search(r'\d(?:[.,]\d+)?\s*%', visible(page.get('content'))))
    nav_only = False
    if not nonchrome and len(visible(page.get('content'))) <= 400:
        from ForecastAgent.readers.material_structure import html_tree
        tree = html_tree(page)
        controls = tree.xpath('//nav|//footer')
        if controls:
            nav_only = tree in controls
            if not nav_only:
                for node in controls: node.drop_tree()
                nav_only = not visible(tree.text_content())
    if nav_only and not structure.get('truncated') and len(visible(page.get('content'))) <= 400 and not page.get('rows') and not header_value:
        result.update(role='navigation_shell', qualifies_full_body=False, usable_text=False)
    elif sections and not nonchrome:
        # A publisher can incorrectly wrap real video/article descriptions in a header.
        result['role'] = 'markup_scope_unknown_retained'
    elif dated_values or page.get('rows'):
        result['role'] = 'dated_or_structured_record_original'
    elif any(section['role'] == 'article' for section in headings) and not prose and not tables and len(visible(page.get('content'))) < 1200:
        # A title/standfirst is useful context; formatting controls do not create paragraphs.
        result.update(role='article_preview', qualifies_full_body=False, usable_text=True)
    else:
        result['role'] = 'body_scope_unverified'
    return result


def applicability(page, url, lead, request):
    """Report literal scope signals only; no missing token becomes a NO verdict."""
    result = copy.deepcopy(profile(page, url))
    heading_text = '\n'.join(row['text'] for row in result.get('headings', []))
    if not heading_text:
        heading_text = '\n'.join(page.get('page_date_metadata', {}).get('headings', [])[:12])
    query_terms = tokens(' '.join(lead.get('search_queries', [])))
    title_terms = tokens(lead.get('title', ''))
    heading_terms = tokens(heading_text)
    body_terms = tokens(page.get('content', ''))
    result['literal_scope'] = {
        'search_title': lead.get('title', ''), 'search_queries': lead.get('search_queries', []),
        'query_terms_in_headings': sorted(query_terms & heading_terms),
        'query_terms_elsewhere_only': sorted((query_terms & body_terms) - heading_terms),
        'title_terms_in_headings': sorted(title_terms & heading_terms),
        'requested_year_literals': sorted(set(re.findall(r'\b20\d{2}\b', request.get('question', '')))),
        'heading_year_literals': sorted(set(re.findall(r'\b20\d{2}\b', heading_text))),
        'translation_or_entailment_checked': False,
    }
    result['scope_review_required'] = bool(query_terms and not query_terms & heading_terms)
    result['automatic_followup_requires_scope_review'] = bool(
        query_terms and not query_terms & heading_terms and len(query_terms & body_terms) >= 2)
    result['scope_warning'] = 'A query match outside headings may be sidebar or background text; inspect exact source context.' if result['scope_review_required'] else None
    return result


def observed_details(page, url, *, limit=8):
    """Return original anchor destinations with their markup/hash provenance."""
    if not 1 <= limit <= 8:
        raise ValueError('At most eight observed details may be projected')
    if page.get('content_type', '').split(';', 1)[0] not in {'text/html', 'application/xhtml+xml'} or not page.get('raw_response_base64'):
        return []
    try:
        from ForecastAgent.readers.material_structure import html_tree, node_role, observable_url
        tree = html_tree(page)
    except Exception:
        return []
    parent = page.get('final_url') or url
    bases = tree.xpath('//base[@href]')
    base = observable_url(bases[0].get('href'), parent) if bases else parent
    if base is None:
        return []  # An invalid/private declared base is not replaced by a guessed public path.
    rows = []
    for node in tree.xpath('//a[@href]')[:2000]:
        role, role_basis = node_role(node)
        destination = observable_url(node.get('href'), base)
        if not destination or not urlsplit(destination).path.lower().endswith(DOCUMENT_SUFFIXES + UNSUPPORTED_DOCUMENT_SUFFIXES):
            continue
        if any(row['url'] == destination for row in rows):
            continue
        supported = urlsplit(destination).path.lower().endswith(DOCUMENT_SUFFIXES)
        rows.append({'url': destination, 'title': visible(node.text_content())[:500],
            'origin': 'observed_document_detail', 'depth': 1, 'parent_url': url,
            'parent_source_sha256': page['sha256'], 'parent_retrieved_at_utc': page.get('retrieved_at_utc'),
            'observed_xpath': tree.getroottree().getpath(node), 'observed_attribute': 'href',
            'resolved_against': base,
            'parent_markup_role': role, 'role_basis': role_basis,
            'automatic_capture_eligible': supported and role not in CHROME,
            'automatic_capture_block_reason': 'unsupported_native_document_format' if not supported else 'chrome_link_requires_review' if role in CHROME else None,
            'relevance_verified': False, 'network_calls': 0})
    return sorted(rows, key=lambda row: (not row['automatic_capture_eligible'], row['url']))[:limit]


def catalog_role(page, url):
    result = profile(page, url)
    if result['structure_status'] != 'available':
        return result
    path = urlsplit(url).path.rstrip('/').split('/')[-1].casefold()
    if path in {'documents', 'downloads', 'publications', 'resources'} and len(observed_details(page, url, limit=8)) >= 3:
        result.update(role='document_catalog', qualifies_full_body=False, usable_text=True,
            catalog_is_not_a_complete_document=True)
    return result
