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
Check EACH target-link reason against the node's own R IDs, not merely its summary.
Do not introduce facts from another node in a link reason. If an inference combines
observations, retain it as a separate explicit hypothesis with its dependencies.
Keep publication/capture dates separate from event dates. A weekday or relative
date needs its own source context; publication time alone cannot date an event.
Distinguish named issuer, city, province, district and affected population. A list
of municipalities is not an order issued by one city. Do not broaden source scope.
For each supplied link-review card, compare the observation period/metric/stage
against THAT target condition: research needs can ask for history, while the
forecast target asks for its eventual result. A historical baseline is not direct
evidence of a future target or an exception whose prerequisites are unknown.
Reconcile reason, role, effect and limitation together. If unsupported, weaken or
remove the link while retaining a valid original observation; no forced linkage.
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
    from ForecastAgent.research_loop import target_logic
    registry = {t['id']:t for t in target_logic.targets(bundle)} if target_logic.enabled(bundle) else {}
    cards, omitted, card_chars = [], [], 0
    for node in nodes:
        for index, link in enumerate(node.get('target_links', [])):
            target = registry.get(link.get('target_id'), {})
            if len(cards) >= 32:
                omitted.append({'node_id':node['id'],'link_index':index})
                continue
            card = {'node_id':node['id'],'link_index':index,
                'own_reference_ids':list(node.get('evidence_ids', [])),
                'target_id':link.get('target_id'), 'target_kind':target.get('kind','unknown'),
                'target_condition':target.get('condition',''),
                'proposed_role':link.get('role'), 'proposed_effect':link.get('effect'),
                'proposed_reason':link.get('reason',''),
                'source_meaning_verified':False}
            size = len(json.dumps(card,ensure_ascii=False))
            if card_chars + size > 6000:
                omitted.append({'node_id':node['id'],'link_index':index,
                    'reason':'Exact link/target condition exceeds review-card character allowance.'})
                continue
            cards.append(card)
            card_chars += size
    return {'policy': 'source_scope_and_supersession_review_v2', 'signals': signals,
        'link_review_cards':cards,'omitted_link_review_cards':omitted,
        'link_card_chars':card_chars,'link_card_char_cap':6000,
        'scope':'Own source IDs and exact frozen target, not cross-node factual support or semantic proof.',
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
