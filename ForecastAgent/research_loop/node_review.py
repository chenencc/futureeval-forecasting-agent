"""Advisory source-scope review signals; never certify or delete graph facts."""
import json
import re
from datetime import datetime

GUIDE = '''
On every update, recheck existing interpretations against their own supplied R IDs.
Do not copy a date, event or metric from another node onto unrelated references.
Newer information may invalidate an old interpretation or limitation even when the
old historical observation remains valid. Replace that node using its existing ID
to correct its scope, or retire explicitly with a reason. Adding a new current node
does not correct an old claim that it was the latest or that no newer data exists.
Latest/current/absence claims apply only to the exact inspected source scope, never
to all available material unless coverage establishes it. Use dated row context
when supplied. A date in a header or navigation is not the observation date.
Review signals are prompts to inspect originals, not proof of contradiction.
Neither a source receipt nor a clean signal certifies interpretation correctness.
'''


def brief(bundle):
    nodes = (bundle.get('research_loop', {}).get('current') or {}).get('nodes', [])
    groups = {}
    for node in nodes:
        explanation = node.get('interpretation', '').strip()
        if explanation:
            groups.setdefault(explanation, []).append(node)
    signals = []
    for node in nodes:
        explanation = ' '.join((node.get('interpretation', ''), node.get('limitation', '')))
        flags = []
        if re.search(r'\b(?:most recent|latest|current(?:ly)?|no (?:direct )?(?:data|observations)|data only available)\b', explanation, re.I):
            flags.append('recency_or_coverage_claim_requires_original_scope')
        peers = groups.get(node.get('interpretation', '').strip(), [])
        signature = {(b.get('url'), b.get('body_sha256'), b.get('start'), b.get('end')) for b in node.get('bindings', [])}
        if any({(b.get('url'), b.get('body_sha256'), b.get('start'), b.get('end')) for b in other.get('bindings', [])} != signature for other in peers):
            flags.append('same_interpretation_different_source_scope')
        if flags:
            signals.append({'node_id': node['id'], 'signals': flags,
                'reference_ids': list(node.get('evidence_ids', [])), 'meaning_verified': False})
    return {'policy': 'source_scope_and_supersession_review_v1', 'signals': signals,
        'signals_are_advisory': True, 'automatic_retirement': False, 'meaning_verified': False}


def dated_row_context(spans, as_of):
    """Select whole spans containing dated JSON rows, not inferred latest facts."""
    try:
        cutoff = datetime.fromisoformat(str(as_of).replace('Z', '+00:00')).date().isoformat()
    except (ValueError, TypeError):
        return []
    ranked = []
    for span in spans:
        dates = []
        for line in span.get('text', '').splitlines():
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(row, dict):
                continue
            date = row.get('date')
            if isinstance(date, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
                try:
                    datetime.fromisoformat(date)
                except ValueError:
                    continue
                if date <= cutoff:
                    dates.append(date)
        if dates:
            ranked.append((max(dates), span))
    # A calendar date is merely a candidate reading anchor, not truth/metric proof.
    return [span for _, span in sorted(ranked, key=lambda pair: (pair[0], pair[1]['start']), reverse=True)]
