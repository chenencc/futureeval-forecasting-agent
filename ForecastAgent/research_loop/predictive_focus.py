"""Opt-in obtainable-gap routing and interpretation risk signals, not truth checks."""
import copy
import re

from ForecastAgent.research_loop.schema import NEED
from ForecastAgent.runtime.contracts import check_schema

FIELD = 'research_predictive_focus_policy'
POLICY = 'obtainable_inputs_and_scope_risks_v1'
GUIDE = '''
Predictive focus: separate the resolution condition from inputs that inform it.
For each material_request declare information_state and purpose. information_state
is saved_unread, obtainable, or future_unknown. purpose is definition, baseline,
history, indicator, counterevidence, or future_outcome. saved_unread names an exact
saved source_url and uses local reading; it must not cause another network fetch.
obtainable names information public now (source_url may be empty for discovery).
future_unknown uses future_outcome and availability=future_event, source_url='';
retain uncertainty and seek a present baseline/indicator rather than its final
future result. Other requests cannot have future_event availability. A missing
date/window/metric in delivered text is a scope limitation, not proof of absence.
Interpretation risk signals check syntax/support anchors only. Neither a clean
signal nor an accepted ID certifies meaning. Recheck the bound ORIGINALS before
asserting intention, nonoccurrence, actual-vs-guidance, or an exact target value.
'''
STATES = ('saved_unread', 'obtainable', 'future_unknown')
PURPOSES = ('definition', 'baseline', 'history', 'indicator', 'counterevidence', 'future_outcome')
EXTENSIONS = {
    'information_state': {'type':'string', 'enum':list(STATES)},
    'purpose': {'type':'string', 'enum':list(PURPOSES)},
    'source_url': {'type':'string', 'maxLength':2000},
}


def enabled(bundle):
    value = bundle.get('request', {}).get(FIELD, 'disabled')
    if value not in {'disabled', POLICY}:
        raise ValueError('Unknown predictive focus policy')
    if value == POLICY:
        from ForecastAgent.research_loop import reference_map
        if not reference_map.enabled(bundle):
            raise ValueError('Predictive focus requires the reference-bound interface')
        return True
    return False


def schema(base):
    result = copy.deepcopy(base)
    need = result['properties']['material_requests']['items']
    need['properties'].update(copy.deepcopy(EXTENSIONS))
    need['required'] = list(dict.fromkeys(need['required'] + list(EXTENSIONS)))
    return result


def validate_need(bundle, need):
    required = copy.deepcopy(NEED)
    required['required'] += list(EXTENSIONS)
    check_schema(need, required)
    state = need['information_state']
    future = need['purpose'] == 'future_outcome'
    if (state == 'future_unknown') != future or (state == 'future_unknown') != (need.get('availability') == 'future_event'):
        raise ValueError('Future outcome/state/availability must agree; choose an observable input otherwise')
    if state == 'future_unknown' and need['source_url']:
        raise ValueError('An available landing page does not make a future outcome obtainable')
    if state == 'saved_unread':
        page = bundle.get('pages', {}).get(need['source_url'])
        from ForecastAgent.readers.quality import body_diagnostics
        if not page or not body_diagnostics(page.get('content', ''))['usable_text']:
            raise ValueError('Saved-unread request requires an exact saved readable source')
    return state


def network_candidate(bundle, need):
    if not enabled(bundle):
        return need.get('availability') != 'future_event'
    # Invalid or missing declarations are visible gaps, never network permission.
    try:
        return validate_need(bundle, {k:v for k,v in need.items() if k in NEED['properties']}) == 'obtainable'
    except (ValueError, TypeError, KeyError):
        return False


def audit(bundle, nodes=None, requests=None):
    """Report conservative review signals; never reject original text by heuristics."""
    current = bundle.get('research_loop', {}).get('current') or {}
    nodes = current.get('nodes', []) if nodes is None else nodes
    requests = current.get('material_requests', []) if requests is None else requests
    rules = '\n'.join(str(bundle.get('request', {}).get(k, '')) for k in
                     ('question', 'resolution_criteria', 'fine_print'))
    risks = []
    for n in nodes:
        explanation = ' '.join([n.get('interpretation', ''), n.get('claim', '') if n['kind'] != 'observation' else ''] +
                               [l.get('reason', '') for l in n.get('target_links', [])])
        original = '\n'.join(b.get('text', '') for b in n.get('bindings', []))
        flags = []
        if n['kind'] in {'driver','assumption'} and not re.search(r'\b(?:if|may|might|could|would|hypothesis|assume|potential)\b', explanation, re.I) and re.search(
                r'\b(?:has|have|had|is|are|was|were|will)\b', explanation, re.I):
            flags.append('unbound_hypothesis_contains_assertion')
        if re.search(r'\b(?:no|never|none|not found|absence|absent|has not|have not)\b', explanation, re.I):
            flags.append('negative_or_absence_inference_requires_scope_review')
        if re.search(r'\b(?:intends?|intention|plans? to|decided to)\b', explanation, re.I):
            flags.append('intent_or_stage_inference_requires_original_review')
        numbers = sorted({x for x in re.findall(r'(?<![\w])\d+(?:[.,]\d+)*(?:%|\b)', explanation)
                          if not re.search(r'(?<![\w])' + re.escape(x) + r'(?![\w])', original + '\n' + rules)})
        if numbers:
            flags.append('numeric_literal_not_in_selected_originals_or_rules')
        risks.append({'node_id':n['id'], 'selected_original_ids':n.get('evidence_ids', []),
                      'signals':flags, 'unanchored_numeric_literals':numbers,
                      'meaning_verified':False, 'review_required':bool(flags)})
    gaps = []
    for r in requests:
        try:
            state = validate_need(bundle, r)
            gaps.append({'target':r['target'], 'state':state, 'network_candidate':state == 'obtainable'})
        except (ValueError, TypeError, KeyError) as exc:
            gaps.append({'target':r.get('target'), 'state':'invalid_declaration', 'error':str(exc), 'network_candidate':False})
    return {'policy':POLICY, 'node_review_signals':risks, 'material_requests':gaps,
            'meaning_verified':False, 'heuristics_can_have_false_positives':True,
            'originals_rejected_by_risk_signals':False}


def brief(bundle, nodes=None, requests=None):
    """Keep full diagnostics in the journal, without crowding out original text."""
    report = audit(bundle, nodes, requests)
    return {'policy':POLICY, 'review_signals':[
        {'node_id':r['node_id'], 'signals':r['signals']} for r in report['node_review_signals'] if r['review_required']],
        'invalid_request_count':sum(r['state']=='invalid_declaration' for r in report['material_requests']),
        'meaning_verified':False, 'signals_are_advisory':True}
