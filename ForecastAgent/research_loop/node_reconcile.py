"""Isolate each observation's original scope before revising its annotations."""
import copy
import json

from ForecastAgent.runtime.contracts import check_schema
from ForecastAgent.research_loop import reference_map, state, target_logic

NAME = 'reconcile_observation'
SYSTEM = '''Review ONE existing observation against ONLY its supplied originals.
The old interpretation and link reasons are fallible candidates, not evidence.
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
        ids = [i for i in originals if i in own | context]
        result.append({'node_id': node['id'], 'candidate': reference_map.input_node(node),
            'own_reference_ids': list(node.get('evidence_ids', [])),
            'selectable_reference_ids': ids,
            'missing_own_reference_ids': sorted(own - originals.keys()),
            'originals': [copy.deepcopy(originals[i]) for i in ids],
            'scope': 'Only this node own originals and supplied same-version context; no other node evidence.',
            'interpretation_verified': False})
    return result


def tool(card, map_tool):
    node_schema = map_tool['function']['parameters']['properties']['nodes']['items']
    fields = ('evidence_ids', 'interpretation', 'limitation', 'applicability', 'target_links')
    properties = {k: copy.deepcopy(node_schema['properties'][k]) for k in fields}
    properties.update(node_id={'type': 'string', 'enum': [card['node_id']]},
        verdict={'type': 'string', 'enum': ['retained', 'corrected', 'insufficient', 'deferred']},
        review_reason={'type': 'string', 'maxLength': 240})
    return {'type': 'function', 'function': {'name': NAME,
        'description': 'Review one source-bound observation; retain originals and repair fallible annotations.',
        'parameters': {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}}}


def messages(bundle, card):
    targets = target_logic.targets(bundle) if target_logic.enabled(bundle) else []
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': json.dumps({
        'question': {k: bundle['request'].get(k, '') for k in state.RULE_FIELDS},
        'as_of_utc': bundle['request'].get('as_of_utc'),
        'target_conditions': targets, 'observation_to_review': card}, ensure_ascii=False)}]


def proposal(bundle, packet, card, answer, map_tool):
    """Translate one bounded review to an ordinary merge, without truth certification."""
    check_schema(answer, tool(card, map_tool)['function']['parameters'])
    selected = answer['evidence_ids']
    if not answer['review_reason'].strip():
        raise ValueError('A node review needs a nonempty review reason')
    if len(selected) != len(set(selected)) or any(i not in card['selectable_reference_ids'] for i in selected):
        raise ValueError('Observation review selected a reference outside its own supplied scope')
    if answer['verdict'] == 'deferred':
        return None, {'node_id': card['node_id'], 'status': 'deferred',
            'meaning_verified': False, 'reason': answer['review_reason']}
    if not selected:
        raise ValueError('A reviewed observation requires a supplied own-source reference')
    node = copy.deepcopy(card['candidate'])
    for field in ('evidence_ids', 'interpretation', 'limitation', 'applicability', 'target_links'):
        node[field] = copy.deepcopy(answer[field])
    annotations = ('evidence_ids', 'interpretation', 'limitation', 'applicability', 'target_links')
    changed = any(node[k] != card['candidate'].get(k) for k in annotations)
    if answer['verdict'] == 'retained' and changed:
        raise ValueError('Retained verdict cannot conceal a changed observation')
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
        'verdict': answer['verdict'], 'changed': changed,
        'selected_reference_ids': selected, 'missing_own_reference_ids': card['missing_own_reference_ids'],
        'meaning_verified': False, 'reason': answer['review_reason']}
