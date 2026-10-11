"""Isolate each observation's original scope before revising its annotations."""
import copy
import json

from ForecastAgent.runtime.contracts import check_schema
from ForecastAgent.research_loop import reference_map, state, target_logic

NAME = 'reconcile_observation'
BLIND_SYSTEM = '''Read the supplied originals and reconstruct ONE source-local observation.
Return one reconcile_observation call. No prior interpretation is supplied.
Do not search, forecast, resolve the event, or infer from unseen image pixels.
State what the text actually establishes. A photo caption establishes its caption,
not normal operation or absence of problems. Navigation is not operational status.
Separate publication date from event date, issuer from reporting authority, and
affected municipalities from the municipality that issued an order. When text
does not establish an event date or issuer, leave that premise uncertain.
Limit absence and coverage claims to supplied excerpts and as_of_utc, not the
entire website or future reporting. Preserve literal numeric baselines and units.
A historical measurement can directly answer a historical research need while
remaining baseline/indicator evidence for an unknown future forecast target.
Select only supplied reference IDs. No other node evidence is available.
Describe measurements available in these originals. Do not claim they are the
latest across the archive or market: this request cannot compare other sources.
Missing future reporting is unknown, not absence as of a future deadline.
Keep interpretation<=240, limitation<=180, link reasons<=240, review_reason<=240
CHARACTERS. Write short annotations, with uncertainty in limitation. Returned
annotations are provisional model interpretations, not verified truth.
'''
SYSTEM = '''Review ONE existing observation against ONLY its supplied originals.
The old interpretation and link reasons are fallible candidates, not evidence.
Literal record boundaries are reading context, not certified relationships.
Check field associations inside the selected record; do not borrow the next
record's title/value. If the saved formatting is ambiguous, preserve uncertainty.
Return one reconcile_observation call. Do not search, forecast or resolve the event.
Keep a valid historical observation even when a future target is unknown.
Check every factual premise in interpretation AND each link reason. Repair all
unsupported premises, not just the effect label. Captions are not image pixels;
navigation is not operational status. Publication dates do not date an event.
Separate the named issuer from reporting authority and affected municipalities.
Use a date only when its event role is established by this source's own context;
otherwise describe the uncertainty. Do not claim coverage beyond supplied excerpts
or beyond as_of_utc. Cite no outlet or measurement from another node.
Choose retained only after reviewing all old premises; corrected when revised;
insufficient when useful original scope remains uncertain; deferred when unread.
Select evidence_ids only from selectable_reference_ids. No new references or
cross-node evidence is available. Preserve useful numeric dates/values when literal
support exists. A baseline link to a research need may be direct, while a future
forecast link is baseline/indicator, never a realized future result.
Keep interpretation<=240, limitation<=180, each link reason<=240 and review_reason
<=240 CHARACTERS. Prefer shorter text. Review verdicts remain model opinions.
'''


def cards(bundle, packet):
    originals = {s['evidence_id']: s for s in packet['evidence'] + packet['context_evidence']}
    result = []
    for node in (bundle['research_loop'].get('current') or {}).get('nodes', []):
        if node['kind'] != 'observation':
            continue
        own = set(node.get('evidence_ids', []))
        versions = {(originals[i]['url'], originals[i]['body_sha256']) for i in own if i in originals}
        context = {i for ident in own for i in packet.get('context_reference_ids', {}).get(ident, [])
                   if i in originals and (originals[i]['url'], originals[i]['body_sha256']) in versions}
        # Only actually delivered, same-version record windows may expand a
        # short or older alias. Overlap identifies local reading context, never
        # a fact relationship or an independently verified record association.
        for ident, span in originals.items():
            if span.get('record_delimiter') != 'multiline_markdown_link_end':
                continue
            for own_id in own & originals.keys():
                selected = originals[own_id]
                if (all(span.get(k) == selected.get(k) for k in
                        ('url','body_sha256','coordinate_space','view_sha256')) and
                        span['start'] < selected['end'] and selected['start'] < span['end']):
                    context.add(ident)
        ids = [i for i in originals if i in own | context]
        result.append({'node_id': node['id'], 'candidate': reference_map.input_node(node),
            'own_reference_ids': list(node.get('evidence_ids', [])),
            'selectable_reference_ids': ids,
            'missing_own_reference_ids': sorted(own - originals.keys()),
            'originals': [copy.deepcopy(originals[i]) for i in ids],
            'scope': 'Only this node own originals and delivered same-version context; record syntax does not certify meaning. No other node evidence.',
            'interpretation_verified': False})
    return result


def tool(card, map_tool, *, blind=False):
    node_schema = map_tool['function']['parameters']['properties']['nodes']['items']
    fields = ('evidence_ids', 'interpretation', 'limitation', 'applicability', 'target_links')
    properties = {k: copy.deepcopy(node_schema['properties'][k]) for k in fields}
    properties.update(node_id={'type': 'string', 'enum': [card['node_id']]},
        verdict={'type': 'string', 'enum': ['retained', 'corrected', 'insufficient', 'deferred']},
        review_reason={'type': 'string', 'maxLength': 240})
    if blind:
        properties.pop('verdict')
    return {'type': 'function', 'function': {'name': NAME,
        'description': 'Review one source-bound observation; retain originals and repair fallible annotations.',
        'parameters': {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}}}


def messages(bundle, card, *, blind=False, format_feedback=None):
    targets = target_logic.targets(bundle) if target_logic.enabled(bundle) else []
    supplied = ({k: copy.deepcopy(card[k]) for k in ('node_id', 'own_reference_ids',
        'selectable_reference_ids', 'missing_own_reference_ids', 'originals', 'scope')}
        if blind else card)
    payload = {
        'question': {k: bundle['request'].get(k, '') for k in state.RULE_FIELDS},
        'as_of_utc': bundle['request'].get('as_of_utc'),
        'target_conditions': targets, 'observation_to_review': supplied,
        'scope_limits': {'coverage': 'supplied_originals_only', 'cross_source_latestness_verified': False}}
    if format_feedback is not None:
        payload['annotation_format_repair'] = copy.deepcopy(format_feedback)
    system = BLIND_SYSTEM if blind else SYSTEM
    from ForecastAgent.research_loop import predictive_focus
    if predictive_focus.enabled(bundle):
        system += predictive_focus.PREMISE_GUIDE
        if not blind:
            # Reuse the source/rule origin audit on this candidate's own
            # actually delivered bindings only. Other nodes never supply proof.
            own = set(card['own_reference_ids'])
            candidate = {**copy.deepcopy(card['candidate']), 'id':card['node_id'],
                         'kind':'observation', 'bindings':[
                             copy.deepcopy(s) for s in card['originals'] if s['evidence_id'] in own]}
            payload['candidate_literal_origins'] = predictive_focus.audit(bundle, [candidate], [])
    return [{'role': 'system', 'content': system},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]


def format_feedback(bundle, card, answer, map_tool, *, blind=False):
    """Only well-identified annotation drafts qualify for a bounded repair call."""
    schema = tool(card, map_tool, blind=blind)['function']['parameters']
    if not isinstance(answer, dict) or set(answer) != set(schema['required']):
        return None
    selected = answer.get('evidence_ids')
    if answer.get('node_id') != card['node_id'] or not isinstance(selected, list) or not selected:
        return None
    if any(not isinstance(i,str) or i not in card['selectable_reference_ids'] for i in selected) or len(set(selected))!=len(selected):
        return None
    loose = copy.deepcopy(schema)
    editable = ('interpretation','limitation','applicability','review_reason')
    for field in editable:
        if not isinstance(answer[field],str):
            return None
        loose['properties'][field] = {'type':'string'}
    links = answer.get('target_links')
    if not isinstance(links,list):
        return None
    known_targets = {t['id'] for t in target_logic.targets(bundle)} if target_logic.enabled(bundle) else set()
    if any(not isinstance(link,dict) or link.get('target_id') not in known_targets for link in links):
        return None
    link_schema = loose['properties']['target_links']['items']
    for field in ('role','effect','reason'):
        link_schema['properties'][field] = {'type':'string'}
    try:
        check_schema(answer,loose)
    except (ValueError,KeyError,TypeError):
        return None
    errors=[]
    def examine(value,rule,path):
        try:
            check_schema(value,rule,path)
        except (ValueError,KeyError,TypeError) as exc:
            errors.append({'field':path,'error':str(exc),'characters':len(value),
                'max_characters':rule.get('maxLength'),'allowed_values':rule.get('enum')})
    for field in editable:
        examine(answer[field],schema['properties'][field],field)
    for position,link in enumerate(links):
        for field in ('role','effect','reason'):
            examine(link[field],schema['properties']['target_links']['items']['properties'][field],
                'target_links.'+str(position)+'.'+field)
    if not errors:
        return None
    return {'draft':copy.deepcopy(answer),'errors':errors,
        'instruction':'Return a full corrected call within declared limits. No text clipping or invented facts. '
            'Keep node_id, evidence_ids and ordered target_ids EXACTLY unchanged. '
            'Repair annotation format using the same originals; uncertainty must remain explicit.',
        'semantic_correctness_verified':False}


def verify_repair_core(draft,answer):
    """Annotation recovery must not silently change references or target identity."""
    for field in ('node_id','evidence_ids'):
        if draft.get(field)!=answer.get(field):
            raise ValueError('Annotation repair changed frozen '+field)
    targets=lambda item:[link.get('target_id') for link in item.get('target_links',[])]
    if targets(draft)!=targets(answer):
        raise ValueError('Annotation repair changed frozen target IDs')


def proposal(bundle, packet, card, answer, map_tool, *, blind=False):
    """Translate one bounded review to an ordinary merge, without truth certification."""
    check_schema(answer, tool(card, map_tool, blind=blind)['function']['parameters'])
    selected = answer['evidence_ids']
    if not answer['review_reason'].strip():
        raise ValueError('A node review needs a nonempty review reason')
    if len(selected) != len(set(selected)) or any(i not in card['selectable_reference_ids'] for i in selected):
        raise ValueError('Observation review selected a reference outside its own supplied scope')
    if not blind and answer['verdict'] == 'deferred':
        return None, {'node_id': card['node_id'], 'status': 'deferred',
            'meaning_verified': False, 'reason': answer['review_reason']}
    if not selected:
        raise ValueError('A reviewed observation requires a supplied own-source reference')
    node = copy.deepcopy(card['candidate'])
    for field in ('evidence_ids', 'interpretation', 'limitation', 'applicability', 'target_links'):
        node[field] = copy.deepcopy(answer[field])
    annotations = ('evidence_ids', 'interpretation', 'limitation', 'applicability', 'target_links')
    changed_fields = [k for k in annotations if node[k] != card['candidate'].get(k)]
    changed = bool(changed_fields)
    claimed_verdict = answer.get('verdict')
    mismatch = claimed_verdict == 'retained' and changed
    verdict = ('corrected' if changed else 'retained') if blind or mismatch else claimed_verdict
    current = bundle['research_loop']['current']
    args = {'expected_revision': bundle['research_loop']['revision'],
        'revision_kind': 'interpretation_correction', 'material_sha256': packet['material_sha256'],
        'nodes': [node], 'relations': [], 'supporting_path': current.get('supporting_path', ''),
        'alternative_path': current.get('alternative_path', ''),
        'material_requests': [],
        'retired_node_ids': [], 'revision_reason': answer['review_reason'], 'update_mode': 'merge',
        'material_reviews': []}
    allowed = map_tool['function']['parameters']['properties']
    args = {k: v for k, v in args.items() if k in allowed}
    check_schema(args, map_tool['function']['parameters'])
    return args, {'node_id': card['node_id'], 'status': 'model_reviewed',
        'verdict': verdict, 'model_verdict': claimed_verdict, 'verdict_field_mismatch': mismatch,
        'blind_original_reading': blind, 'changed': changed, 'changed_fields': changed_fields,
        'selected_reference_ids': selected, 'missing_own_reference_ids': card['missing_own_reference_ids'],
        'meaning_verified': False, 'reason': answer['review_reason']}
