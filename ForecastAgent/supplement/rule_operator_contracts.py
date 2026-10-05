"""Exact rule/operator provenance and non-destructive observation ledgers.

Lexical cues identify literal text, not semantic scope or entailment. Unsupported
wording remains unassessed. No cue certifies a rule interpretation or outcome.
"""
import copy
import re
from ForecastAgent.supplement import acquisition_contract as base

PATTERNS = {
    'by_boundary': r'\bby\s+[^\n,.;]{1,70}',
    'before_boundary': r'\bbefore\s+[^\n,.;]{1,70}',
    'as_of_date': r'\bas\s+of\s+[^\n,.;]{1,70}',
    'on_date': r'\bon\s+(?:(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}|\d{4}-\d{2}-\d{2})[^\n.;]{0,12}',
    'interval': r'\b(?:throughout|at\s+any\s+point|at\s+least\s+one|continuous\s+period)\b[^\n.;]{0,70}',
    'negation': r'\b(?:not|never|no\s+longer|without)\b[^\n.;]{0,70}',
    'active_state': r'\b(?:is\s+subject\s+to|remains?|in\s+force|currently\s+effective|still\s+applies|legally\s+effective)\b[^\n.;]{0,90}',
    'operative_effect': r'\b(?:prevents?\s+[^\n.;]{0,90}?\s+from|prohibits?|bars?\s+[^\n.;]{0,60}?\s+from)\b[^\n.;]{0,90}',
    'definition': r'\b(?:defined\s+as|definition\s+of|means\s+that)\b[^\n.;]{0,90}'}


def inventory(plan):
    cues = []
    for rule in plan['rule_catalog']:
        for kind, pattern in PATTERNS.items():
            for match in re.finditer(pattern, rule['text'], re.I):
                start, end = rule['start'] + match.start(), rule['start'] + match.end()
                cue = {'kind': kind, 'rule_id': rule['rule_id'], 'field': rule['field'],
                    'field_sha256': rule['field_sha256'], 'start': start, 'end': end,
                    'text': match.group(), 'semantic_scope_verified': False}
                cue['operator_id'] = 'OP-' + base.sha(str((kind, rule['rule_id'], start, end)))[:18]
                cues.append(cue)
    return cues


def verify(cues, bundle):
    for cue in cues:
        text = bundle['request'].get(cue['field']) or ''
        if base.sha(text) != cue['field_sha256'] or text[cue['start']:cue['end']] != cue['text']:
            raise ValueError('original_operator_span_changed')


def risks(need, bindings):
    condition = need['condition']
    flags = []
    kinds = {b['kind'] for b in bindings}
    if re.search(r'\bas\s+of\b', condition, re.I) and not kinds.intersection(('as_of_date', 'on_date')):
        flags.append('snapshot_operator_not_bound_to_original_rule')
    if re.search(r'\b(?:not|never|no\s+longer)\b', condition, re.I) and 'negation' not in kinds:
        flags.append('negative_operator_not_bound_to_original_rule')
    if any(b['kind'] in ('active_state', 'operative_effect') for b in bindings):
        flags.append('operative_effect_requires_source_review')
    return flags


def ledger(need, rule_inventory):
    rules = {r['rule_id']: r for r in rule_inventory}
    origins = [copy.deepcopy(rules[r]) for r in need['rule_ids']]
    # The model may label a need literal, but that never removes its independent
    # observation object. Whether a literal-only need requires external facts
    # remains unassessed rather than inferred from a fallible type label.
    return {'need_id': need['id'],
        'requested_parameters': {'targets': copy.deepcopy(need['targets']),
            'rule_bindings': origins, 'status': 'requested_constraints_not_observed_values'},
        'observation_obligation': {'original_condition': need['condition'],
            'requirement_status': 'unassessed', 'evidence_status': 'unassessed',
            'may_not_be_removed_by_type_classification': True,
            'actual_value': None, 'actual_value_status': 'not_extracted'},
        'literal_context': {'binding': None, 'status': 'unassessed'}}
