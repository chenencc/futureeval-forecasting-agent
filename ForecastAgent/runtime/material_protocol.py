"""Opt-in V3 control contracts; no semantic verdicts or provider calls."""
import copy
import re
from datetime import datetime

from ForecastAgent.runtime.contracts import ContractError

STRATEGY = 'intelligent_materials_v3'
REVIEW_ROUNDS = 2
TIME_FIELDS = ('open_time', 'close_time', 'scheduled_close_time', 'scheduled_resolve_time')
NO_GAP_ALIASES = {'', 'none', 'no further material needed.'}


def enabled(task):
    return task.bundle['request'].get('acquisition_strategy') == STRATEGY


def rule_metadata(task):
    """Only immutable platform input supplies rule metadata, never source dates."""
    from ForecastAgent.runtime.retrieval import parse_time
    rows = []
    for field in TIME_FIELDS:
        value = task.bundle['request'].get(field)
        parsed = parse_time(value)
        rows.append({'field': field, 'value': value if parsed else None,
                     'state': 'provided' if parsed else 'unknown',
                     'origin': 'immutable_request', 'inferred': False})
    return {'fields': rows, 'scope': 'Metadata references are authoritative only for their named field. '
        'Source publication/event dates and research queries cannot replace unknown rule boundaries. '
        'Original resolution text remains authoritative; plan prose is an unverified material target.'}


def dates(text):
    """Explicit calendar-date lineage, not a natural-language rule interpreter."""
    months = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    patterns = [(r'\b\d{4}-\d{2}-\d{2}(?!\d)', ('%Y-%m-%d',)),
                (rf'\b{months}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?[,]?\s+\d{{4}}\b', ('%B %d %Y', '%b %d %Y')),
                (rf'\b\d{{1,2}}\s+{months}\.?\s+\d{{4}}\b', ('%d %B %Y', '%d %b %Y'))]
    result = set()
    for pattern, formats in patterns:
        for match in re.finditer(pattern, text, re.I):
            value = re.sub(r'(?<=\d)(st|nd|rd|th)\b', '', match.group(), flags=re.I)
            value = value.replace(',', '').replace('.', '')
            for fmt in formats:
                try:
                    result.add(datetime.strptime(value, fmt).date().isoformat())
                    break
                except ValueError:
                    continue
    return result


def validate_targets(task, needs):
    """Atomic references; research dates belong in queries, not target rules."""
    from ForecastAgent.runtime.intelligent_acquisition import FIELDS
    metadata = {r['field']: r for r in rule_metadata(task)['fields']}
    original = '\n'.join([str(task.bundle['request'].get(f) or '') for f in FIELDS]
                         + [r['value'] for r in metadata.values() if r['state']=='provided'])
    allowed = dates(original)
    bindings = []
    for index, need in enumerate(needs):
        deps = need.get('rule_time_fields')
        if not isinstance(deps, list) or len(deps) > len(TIME_FIELDS) or any(f not in metadata for f in deps):
            raise ContractError('rule_metadata_reference', f'needs[{index}].rule_time_fields',
                'List platform time fields required by this target, or [] if no metadata boundary is required. Never supply an inferred value.', list(TIME_FIELDS))
        unsupported = dates(need['condition']) - allowed
        if unsupported:
            raise ContractError('unsupported_target_date', f'needs[{index}].condition',
                'Target dates must occur in immutable question text or platform metadata. '
                'Keep unknown boundaries symbolic; source-event dates may be research queries only. Unsupported: '+', '.join(sorted(unsupported)))
        bindings.append([copy.deepcopy(metadata[f]) for f in dict.fromkeys(deps)])
    for need, refs in zip(needs, bindings):
        need['rule_metadata_bindings'] = refs


def unknown_dependencies(need):
    return [r['field'] for r in need.get('rule_metadata_bindings', []) if r['state'] == 'unknown']


def assessment_schema(schema):
    result = copy.deepcopy(schema)
    item = result['properties']['items']['items']
    item['properties']['missing_items'] = {'type': 'array', 'maxItems': 8,
        'items': {'type': 'string', 'maxLength': 1200},
        'description': 'Use [] for adequate; otherwise list concrete material or rule-metadata gaps.'}
    item['properties']['missing_material']['description'] = 'Optional legacy field. Omit; use missing_items. Real gap text cannot coexist with adequate.'
    item['required'] = [k for k in item['required'] if k != 'missing_material'] + ['missing_items']
    return result


def normalize_assessment(item, need):
    """Narrow syntax aliases only; never erase an actual missing-material claim."""
    result = copy.deepcopy(item)
    gaps = result['missing_items']
    if any(not s.strip() for s in gaps):
        raise ContractError('empty_material_gap', 'missing_items', 'Use [] or substantive gap strings; do not add blank items.')
    legacy = result.get('missing_material', '')
    if legacy.strip().lower() not in NO_GAP_ALIASES:
        gaps = list(dict.fromkeys(gaps + [legacy.strip()]))
    if result['status'] == 'adequate' and (gaps or unknown_dependencies(need)):
        raise ContractError('inconsistent_material_assessment', 'missing_items',
            'Adequate requires [] and resolved required metadata. Preserve actual gaps, including: '+', '.join(unknown_dependencies(need)))
    if result['status'] != 'adequate' and not gaps:
        raise ContractError('missing_material_gap', 'missing_items', 'List a concrete missing date, document, row or identity.')
    result['missing_items'] = gaps
    result['missing_material'] = '\n'.join(gaps)
    return result


def closure_ready(task):
    """Agent-declared target completion; not full recall or factual verification."""
    if not enabled(task) or not task.bundle.get('plan') or task.bundle.get('result'):
        return False
    from ForecastAgent.runtime.intelligent_acquisition import frontier
    from ForecastAgent.runtime.needs import active_needs
    from ForecastAgent.runtime.search_policy import requirement
    from ForecastAgent.runtime.collection_actions import primary_rescue
    active = {n['id'] for n in active_needs(task.bundle)}
    rows = [n for n in frontier(task)['needs'] if n['need_id'] in active]
    obligation = requirement(task)
    return bool(rows and not primary_rescue(task)
        and not (obligation['required'] and not obligation['attempt_requirement_met'])
        and all(n['agent_assessment'] and n['agent_assessment']['status'] == 'adequate'
                and not n['assessment_stale'] and not n.get('unknown_rule_fields') for n in rows))
