"""Bounded field-specific corrections drawn only from the visible exact spans."""
import json
import re

from ForecastAgent.research_loop.acceptance import MapAcceptanceError
from ForecastAgent.research_loop.state import bind_node

DATE = re.compile(r'\b(?:\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4}|'
    r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
    r'(?:\s+\d{1,2},?)?\s+\d{4})\b')


def correction(message, error, material, allowed):
    """Suggest corrections; do not change a claim, reference or event date."""
    result = {'status': 'rejected', 'error': str(error)[:1200],
        'instruction': 'Return one complete corrected update_research_state call using the same original text. '
            'Never invent observations or truncate a reference list to preserve an unsupported claim. '
            'If evidence is irrelevant or insufficient, return unknown-only nodes with real gap reasons, '
            'no relations, and both uncertainty paths. Missing evidence is not nonoccurrence. '
            'Default event_time to empty and time_status=unknown; dates below are literal text candidates, '
            'not proof of their event role. Keep at most three references per node.',
        'envelope_errors': [], 'node_errors': [], 'new_material_added': False}
    try:
        proposal = json.loads(message['tool_calls'][0]['function']['arguments'])
    except (ValueError, KeyError, TypeError, IndexError):
        return result
    if not isinstance(proposal, dict):
        return result
    allowed = set(allowed)
    for field in ('supporting_path', 'alternative_path', 'revision_reason'):
        value = proposal.get(field)
        if not isinstance(value, str) or not value.strip():
            result['envelope_errors'].append({'field': field, 'error': 'Must be an explicit nonblank path or reason.'})
    rejected = list(error.report['rejected']) if isinstance(error, MapAcceptanceError) else []
    # Envelope rejection must not conceal independently inspectable node failures.
    nodes = proposal.get('nodes')
    for index, node in enumerate(nodes if isinstance(nodes, list) else []):
        if any(r['section'] == 'nodes' and r['index'] == index for r in rejected):
            continue
        try:
            bind_node(node, material, allowed)
        except (ValueError, KeyError, TypeError) as exc:
            rejected.append({'section': 'nodes', 'index': index,
                'node_id': node.get('id') if isinstance(node, dict) else None, 'error': str(exc)})
    for entry in rejected[:8]:
        detail = {k: entry[k] for k in ('section', 'index', 'node_id', 'error')}
        if entry['section'] == 'nodes':
            node = proposal.get('nodes', [])[entry['index']]
            if isinstance(node, dict):
                refs = node.get('evidence_ids')
                refs = refs if isinstance(refs, list) else []
                detail.update(event_time=node.get('event_time'), time_status=node.get('time_status'),
                    reference_count=len(refs))
                detail['safe_timing_action'] = ('Use a separately supplied visible reference containing '
                    'the literal event date, or leave event_time empty and time_status unknown. '
                    'Recheck the claim against its own source row; do not borrow a header date.')
                detail['literal_dates_in_bound_visible_spans'] = []
                for ref in refs[:3]:
                    if not isinstance(ref, str) or ref not in allowed or ref not in material['spans']:
                        continue
                    span = material['spans'][ref]
                    detail['literal_dates_in_bound_visible_spans'].append({'evidence_id': ref,
                        'date_phrases': list(dict.fromkeys(DATE.findall(span['text'])))[:5],
                        'role_verified': False})
        result['node_errors'].append(detail)
    return result
