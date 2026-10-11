"""Observed DOM scope for already delivered text; no event inference or fetch."""
import copy
import json
import re

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.readers.material_structure import html_tree, source_sections, observable_url

VERSION = 'saved_html_scope_v2'
EXCLUDED = {'navigation', 'footer', 'copyright', 'header', 'aside'}
BYTE_CAP = 10000


def normalized(value):
    return re.sub(r'\s+', ' ', value).strip()


def scopes(snapshot, selected_text):
    """Retain literal ancestor headings/links; repeated text stays ambiguous."""
    structure = source_sections(snapshot)
    tree = html_tree(snapshot)
    base = snapshot.get('final_url') or snapshot['url']
    selected = normalized(selected_text)
    groups = {}
    for row in structure['sections']:
        text = normalized(row['text'])
        if row['role'] in EXCLUDED or len(text) < 4 or text not in selected:
            continue
        found = tree.xpath(row['xpath'])
        if len(found) != 1:
            raise ValueError('Saved DOM coordinate became ambiguous')
        node = found[0]
        ancestry = [node, *node.iterancestors()]
        context = []
        for ancestor in ancestry:
            tag = str(ancestor.tag).lower()
            heading = None
            if tag in {'section', 'article', 'main', 'table'}:
                candidates = ancestor.xpath('./h1 | ./h2 | ./h3 | ./h4 | ./h5 | ./h6 | ./caption | ./header/h1 | ./header/h2 | ./header/h3')
                if candidates:
                    heading = [{'text': normalized(h.text_content())[:500], 'truncated': len(normalized(h.text_content())) > 500,
                        'xpath': tree.getroottree().getpath(h)} for h in candidates[:4]]
            href = ancestor.get('href') if tag == 'a' else None
            if heading or href:
                context.append({'tag': tag, 'xpath': tree.getroottree().getpath(ancestor),
                    'headings': heading or [], 'href_attribute': href,
                    'resolved_link': observable_url(href, base) if href else None})
            if len(context) >= 4:
                break
        if not context:
            continue
        # Responsive layouts may repeat the same literal scope. Keep occurrence
        # proofs, but do not let duplicate markup crowd other scopes out.
        signature = [{k: v for k, v in entry.items() if k != 'xpath' and k != 'headings'} |
            {'headings': [{k: v for k, v in h.items() if k != 'xpath'} for h in entry['headings']]}
            for entry in context]
        key = digest(signature)
        group = groups.setdefault(key, {'scope_id': 'S'+key[:12], 'ancestors': context,
            'matched_texts': [], 'matched_dom_nodes': 0, 'sample_truncated': False,
            'scope_occurrences': [], 'scope_occurrences_truncated': False,
            'source_sha256': snapshot['sha256'], 'matching': 'normalized_whitespace_substring',
            'event_identity_verified': False, 'target_applicability_verified': False})
        group['matched_dom_nodes'] += 1
        paths = [entry['xpath'] for entry in context]
        if paths not in group['scope_occurrences']:
            if len(group['scope_occurrences']) < 4:
                group['scope_occurrences'].append(paths)
            else:
                group['scope_occurrences_truncated'] = True
        if len(group['matched_texts']) < 6:
            group['matched_texts'].append({'text': text[:400], 'xpath': row['xpath'],
                'truncated': len(text) > 400, 'role': row['role']})
        else:
            group['sample_truncated'] = True
    ordered = sorted(groups.values(), key=lambda g: (-g['matched_dom_nodes'],
        -max(len(m['text']) for m in g['matched_texts']), g['scope_id']))
    return {'source_url': base, 'source_sha256': snapshot['sha256'],
        'contexts': ordered, 'structure_truncated': structure['truncated'],
        'packing_priority': 'matched_DOM_node_count_then_matched_text_length',
        'network_calls': 0, 'truth_verified': False}


INSTRUCTION = 'Literal local DOM headings/links around matching saved text; not verified target or event labels. A page can embed other periods/events. Recheck embedded results against local headings/links. Repeated matches remain ambiguous. A page URL does not identify every embedded result.'


def compact(context):
    """Keep literal qualifiers; archive long coordinate proofs outside model input."""
    return {'scope_id': context['scope_id'],
        'headings': [{k: v for k, v in h.items() if k != 'xpath'} for a in context['ancestors'] for h in a['headings']],
        'href_attributes': [a['href_attribute'] for a in context['ancestors'] if a['href_attribute']],
        'matched_texts': [{k: v for k, v in row.items() if k in ('text', 'truncated')} for row in context['matched_texts']],
        'sample_truncated': context['sample_truncated'],
        'repeated_scope': len(context['scope_occurrences']) > 1,
        'scope_occurrences_truncated': context['scope_occurrences_truncated']}


def envelope(entries):
    return {'schema': VERSION, 'sources': entries, 'instruction': INSTRUCTION}


def attach(bundle, original, *, byte_cap=None):
    """Same bounded context sideband for both arms; never replace source originals."""
    enriched = copy.deepcopy(original)
    byte_cap = BYTE_CAP if byte_cap is None else byte_cap
    if type(byte_cap) is not int or not 0 <= byte_cap <= BYTE_CAP:
        raise ValueError('Source context must use a bounded residual byte cap')
    entries, skipped, bindings = [], [], []
    sources = {s['source_id']: s for s in original['sources']}
    groups = {}
    for row in original['evidence']:
        source = sources[row['source_id']]
        groups.setdefault(source['url'], []).append(row['text'])
    for url, pieces in groups.items():
        page = bundle['pages'][url]
        if not page.get('raw_response_base64') or 'html' not in page.get('content_type', '').lower():
            skipped.append({'url': url, 'reason': 'saved_HTML_original_unavailable'})
            continue
        try:
            observed = scopes(page, '\n'.join(pieces))
        except (ValueError, KeyError, TypeError) as exc:
            skipped.append({'url': url, 'reason': 'structure_unavailable', 'error': str(exc)[:200]})
            continue
        selected = []
        for context in observed['contexts']:
            entry = {'source_url': observed['source_url'], 'source_sha256': observed['source_sha256'],
                'structure_truncated': observed['structure_truncated'],
                'context_truncated': True, 'truth_verified': False,
                'contexts': [compact(c) for c in selected+[context]]}
            candidate = entries+[entry]
            if len(json.dumps(envelope(candidate)).encode()) > byte_cap:
                break
            selected.append(context)
        if selected:
            entries.append({**entry, 'contexts': [compact(c) for c in selected],
                'context_truncated': len(selected) != len(observed['contexts'])})
            bindings.append({'source_url': observed['source_url'], 'source_sha256': observed['source_sha256'],
                'contexts': selected})
        elif observed['contexts']:
            skipped.append({'url': url, 'reason': 'context_byte_cap'})
    if entries:
        enriched['source_scope_context'] = envelope(entries)
    if any(enriched[k] != value for k, value in original.items()):
        raise ValueError('Source context changed common original evidence')
    return enriched, {'schema': VERSION, 'sources': len(entries), 'skipped': skipped,
        'sideband_present': bool(entries), 'sideband_sha256': digest(enriched.get('source_scope_context')),
        'bound_contexts': bindings, 'byte_cap': byte_cap,
        'network_calls': 0, 'new_page_bytes': 0, 'semantic_truth_verified': False}
