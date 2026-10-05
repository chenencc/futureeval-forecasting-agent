"""Opt-in material planning and version-bound acquisition assessments.

All semantic assessments are agent declarations. This module has no provider,
forecast, or submission interface.
"""
import copy
from datetime import datetime, timezone

from ForecastAgent.readers.quality import body_diagnostics
from ForecastAgent.readers.saved import version_digest, select
from ForecastAgent.runtime.contracts import ContractError, check_schema
from ForecastAgent.tavily_research import canonical_url
from ForecastAgent.tools.registry import tool

STRATEGY = 'intelligent_materials_v1'
FIELDS = ('question', 'resolution_criteria', 'fine_print', 'background')
STATUSES = ('adequate', 'partial', 'unavailable', 'not_yet_published', 'unreviewed')
TOOLS = [
    tool('inspect_materials', 'Inspect material targets, observed source frontier and version-bound assessments. No network, truth verification, or forecast.',
         {'offset': {'type': 'integer', 'minimum': 0},
          'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, []),
    tool('assess_materials', 'Record material adequacy or a precise gap for existing needs. References must be saved and version-valid; adequate requires an associated exact excerpt. This is an unverified agent assessment, not an event verdict. Batch with other local actions when possible.',
         {'items': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': {
             'type': 'object', 'additionalProperties': False, 'properties': {
                 'need_id': {'type': 'string'},
                 'status': {'type': 'string', 'enum': list(STATUSES)},
                 'source_urls': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 8},
                 'excerpt_ids': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 8},
                 'reason': {'type': 'string', 'minLength': 10, 'maxLength': 1200},
                 'missing_material': {'type': 'string', 'maxLength': 1200},
                 'next_action': {'type': 'string', 'maxLength': 500}},
             'required': ['need_id', 'status', 'source_urls', 'excerpt_ids', 'reason', 'missing_material', 'next_action']}}}, ['items']),
]


def enabled(task):
    return task.bundle['request'].get('acquisition_strategy') == STRATEGY


def validate_strategy(task):
    name = task.bundle['request'].get('acquisition_strategy')
    if name is None:
        return
    if name != STRATEGY or task.bundle['pipeline'] != 'collection' or not task.optimized or task.raw_recall:
        raise ValueError('Intelligent materials require collection_v3 with local reading enabled')


def configure_tools(task, tools):
    if not enabled(task):
        return tools
    tools = copy.deepcopy(tools) + copy.deepcopy(TOOLS)
    for entry in tools:
        if entry['function']['name'] == 'plan_evidence':
            spec = entry['function']['parameters']['properties']['needs']
            spec.update(minItems=1, maxItems=8)
            item = spec['items']
            item['properties']['question_spans'] = {'type': 'array', 'minItems': 1, 'maxItems': 3,
                'items': {'type': 'object', 'additionalProperties': False, 'properties': {
                    'field': {'type': 'string', 'enum': list(FIELDS)},
                    'quote': {'type': 'string', 'minLength': 1, 'maxLength': 1200}},
                    'required': ['field', 'quote']}}
            item['required'] = list(item['required']) + ['question_spans']
            entry['function']['description'] = 'Freeze a small material specification, with exact question_spans for each need. Preserve entity, metric, units and timing. Future outcomes are not present-day source requirements. No outcome verdict.'
    return tools


def validate_plan(task, needs):
    if not enabled(task):
        return
    if not 1 <= len(needs) <= 8:
        raise ContractError('material_plan_size', 'needs', 'Plan one to eight concrete material targets.')
    for need in needs:
        spans = need.get('question_spans')
        if not isinstance(spans, list) or not 1 <= len(spans) <= 3:
            raise ContractError('missing_rule_binding', 'question_spans', 'Bind every need to exact original question text.')
        for span in spans:
            field, quote = span.get('field'), span.get('quote')
            original = task.bundle['request'].get(field)
            if field not in FIELDS or not isinstance(quote, str) or not 1 <= len(quote) <= 1200 or not isinstance(original, str) or quote not in original:
                raise ContractError('invalid_rule_binding', 'question_spans', 'Copy a nonempty exact quote from the named question field. Do not invent a requirement.')


def assess(task, args):
    if not enabled(task) or task.bundle.get('result'):
        raise ContractError('material_assessment_unavailable', 'tool', 'Use this tool only in an unfinished intelligent collection.')
    check_schema(args, TOOLS[1]['function']['parameters'])
    needs = {n['id']: n for n in task.bundle.get('plan') or []}
    if not needs:
        raise ContractError('material_plan_missing', 'tool', 'Freeze the material plan first.')
    proposed = []
    for item in args['items']:
        ident = item['need_id']
        if ident not in needs or any(r['need_id'] == ident for r in proposed):
            raise ContractError('invalid_material_need', 'need_id', 'Use each existing need ID at most once per batch.', list(needs))
        if item['status'] != 'adequate' and not item['missing_material'].strip():
            raise ContractError('missing_material_gap', 'missing_material', 'Name the missing date, row, document, identity or independent material.')
        if item['status'] == 'adequate' and item['missing_material'].strip():
            raise ContractError('inconsistent_material_assessment', 'missing_material', 'An adequate declaration cannot also declare missing material.')
        refs = {}
        for url in item['source_urls']:
            key = canonical_url(url)
            page = task.bundle['pages'].get(key)
            if page is None:
                raise ContractError('unknown_material_source', 'source_urls', 'Reference a saved source key, not a guessed URL.')
            if task.verified_only:
                from ForecastAgent.runtime.collection_v2 import eligible
                if not eligible(page, task.cutoff):
                    raise ContractError('audit_only_material', 'source_urls', 'Historical audit-only material cannot support an assessment.')
            refs[key] = {'raw_sha256': page.get('sha256'), 'parsed_version': version_digest(page)}
        excerpts = {e['id']: e for e in task.bundle.get('excerpts', [])}
        for xid in item['excerpt_ids']:
            excerpt = excerpts.get(xid)
            if not excerpt or ident not in excerpt.get('need_ids', []) or excerpt['url'] not in refs:
                raise ContractError('invalid_material_excerpt', 'excerpt_ids', 'Reference an exact excerpt associated with this need and the supplied source.')
            page = task.bundle['pages'][excerpt['url']]
            _, text, _ = select(task.bundle['pages'], excerpt['url'], excerpt.get('location', {}).get('document_index'))
            if (excerpt.get('source_sha256') != page.get('sha256') or excerpt.get('source_parsed_sha256') != version_digest(page)
                    or text[excerpt['start_char']:excerpt['end_char']] != excerpt['text']):
                raise ContractError('stale_material_excerpt', 'excerpt_ids', 'Locate and bank material from the current saved version.')
        if item['status'] == 'adequate' and (not item['excerpt_ids'] or any(not body_diagnostics(task.bundle['pages'][url].get('content', ''))['usable_text'] for url in refs)):
            raise ContractError('unbanked_material', 'excerpt_ids', 'Adequate requires readable original material and at least one exact associated excerpt; a search snippet or body count is insufficient.')
        proposed.append({**copy.deepcopy(item), 'source_versions': refs, 'semantic_verified': False})
    # Validate the entire batch before mutating durable state.
    stored = task.bundle.setdefault('material_assessments', {})
    events = task.bundle.setdefault('material_assessment_events', [])
    changed = []
    for row in proposed:
        if stored.get(row['need_id']) == row:
            continue
        events.append({'at_utc': datetime.now(timezone.utc).isoformat(), 'previous': stored.get(row['need_id']), 'assessment': row})
        stored[row['need_id']] = row
        changed.append(row['need_id'])
    task.save()
    return {'changed_need_ids': changed, 'semantic_verified': False, 'no_progress': True,
            'instruction': 'Assessment bookkeeping adds no material progress and grants no budget.'}


def frontier(task, offset=0, limit=8):
    b = task.bundle
    observed = task.catalog()
    rows = []
    attempts = {canonical_url(a['url']): a for a in b.get('fetch_attempts', []) if a.get('url')}
    for url, hit in sorted(observed.items()):
        page = b['pages'].get(url)
        rows.append({'url': url, 'title': hit.get('title', ''), 'origin': hit.get('origin', 'search'),
                     'published_date': hit.get('published_date'),
                     'state': 'readable' if page and body_diagnostics(page.get('content', ''))['usable_text'] else 'unreadable' if page else 'attempted' if url in attempts else 'unattempted',
                     'document_count': len(page.get('documents') or []) if page else 0,
                     'chars': len(page.get('content', '')) if page else 0})
    assessments = b.get('material_assessments', {})
    needs = []
    for need in b.get('plan') or []:
        row = assessments.get(need['id'])
        stale = bool(row and any(url not in b['pages'] or version_digest(b['pages'][url]) != ref['parsed_version'] or b['pages'][url].get('sha256') != ref['raw_sha256'] for url, ref in row['source_versions'].items()))
        needs.append({'need_id': need['id'], 'condition': need['condition'], 'priority': need['priority'],
                      'question_spans': need.get('question_spans', []),
                      'banked_excerpt_ids': [e['id'] for e in b.get('excerpts', []) if need['id'] in e.get('need_ids', [])],
                      'agent_assessment': row, 'assessment_stale': stale})
    return {'schema': STRATEGY, 'needs': needs, 'sources': rows[offset:offset+limit], 'source_total': len(rows),
            'next_offset': offset+limit if offset+limit < len(rows) else None,
            'budget_remaining': task.budget(), 'semantic_verified': False,
            'instruction': 'Choose a critical material gap and a concrete available action. Inspect saved rows/passages before another search. Future unpublished outcomes remain explicit gaps, not event absence. Assessments never prove adequacy.'}


def terminal_report(task):
    view = frontier(task, limit=20)
    unresolved = []
    for need in view['needs']:
        row = need['agent_assessment']
        if not row or row['status'] != 'adequate' or need['assessment_stale']:
            unresolved.append({'need_id': need['need_id'], 'priority': need['priority'],
                               'status': 'stale' if need['assessment_stale'] else row['status'] if row else 'unreviewed',
                               'missing_material': row['missing_material'] if row else 'Material adequacy was not assessed by the agent.',
                               'next_action': row['next_action'] if row else ''})
    return {**view, 'unresolved_material_targets': unresolved, 'scope': 'Unverified agent assessment and mechanical source binding; not factual verification or forecast accuracy.'}
