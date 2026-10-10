"""Model-independent, bounded research contracts without a probability head."""


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


def text(maximum=360):
    return {'type': 'string', 'maxLength': maximum}


def array(item, maximum, minimum=0):
    return {'type': 'array', 'items': item, 'maxItems': maximum, 'minItems': minimum}


NODE = obj({'id': text(40), 'kind': {'type': 'string', 'enum':
    ['observation', 'driver', 'assumption', 'unknown']}, 'claim': text(),
    'evidence_ids': array(text(32), 3), 'event_time': text(100),
    'time_status': {'type': 'string', 'enum': ['source_stated', 'hypothesized', 'unknown']},
    'gap_reason': {'type': 'string', 'enum': ['none', 'not_searched', 'not_found',
        'inaccessible', 'not_yet_published', 'ambiguous', 'unreviewed']}})
RELATION = obj({'from_id': text(40), 'to_id': text(40), 'kind': {'type': 'string',
    'enum': ['causal', 'evidential', 'necessary', 'sufficient', 'related', 'contradicts']},
    'rationale': text(240)})
# Optional extensions keep archived v1 proposals readable. The v2 tool requires
# every extension explicitly; absence does not silently become a v2 observation.
NODE_EXTENSIONS = {
    'claim_origin': {'type': 'string', 'enum': ['source_quote', 'hypothesis', 'gap']},
    'interpretation': text(240),
    'event_stage': {'type': 'string', 'enum': ['planned', 'ongoing', 'completed', 'not_applicable', 'unknown']},
    'applicability': {'type': 'string', 'enum': ['target', 'background', 'expectation', 'unknown']},
    'limitation': text(180), 'episode_id': text(40)}
NODE['properties'].update(NODE_EXTENSIONS)
# Optional in archived protocols; the coverage-aware acquisition interface
# requires it explicitly. Literal support does not certify stage semantics.
NODE['properties']['stage_basis'] = text(180)
TARGET_LINK = obj({'target_id': text(40),
    'role': {'type':'string', 'enum':['direct','indicator','baseline','procedure','context','unknown','driver']},
    'effect': {'type':'string', 'enum':['supports','opposes','context','unresolved']},
    'reason': text(240)})
# Optional for archived maps; the opt-in target logic tool requires the field.
NODE['properties']['target_links'] = array(TARGET_LINK, 4)
RELATION['properties'].update(mechanism=text(240), rule_quote=text(240))
NEED = obj({'target': text(260), 'reason': text(260), 'node_ids': array(text(40), 4),
    'role': {'type': 'string', 'enum': ['primary', 'counterevidence', 'gap']},
    'suggested_tool': text(60)})
# Optional on archived maps; the gap-feedback presentation requires these fields.
NEED['properties'].update(decision_impact=text(180),
    importance={'type': 'string', 'enum': ['high', 'medium', 'low']},
    availability={'type': 'string', 'enum': ['available', 'uncertain', 'future_event']})
PROPOSAL = obj({'expected_revision': {'type': 'integer', 'minimum': 0},
    'revision_kind': {'type': 'string', 'enum': ['material_update', 'interpretation_correction']},
    'material_sha256': text(64), 'nodes': array(NODE, 12, 1),
    'relations': array(RELATION, 16), 'supporting_path': text(500),
    'alternative_path': text(500), 'material_requests': array(NEED, 4),
    'retired_node_ids': array(text(40), 12), 'revision_reason': text(300)})

TOOLS = [
    {'type': 'function', 'function': {'name': 'inspect_research_state',
        'description': 'Read the fallible research map and exact saved-body reference handles. Local only; no search, no truth certification. Paginate or query omitted spans.',
        'parameters': {'type': 'object', 'properties': {'offset': {'type': 'integer', 'minimum': 0},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 4}, 'query': text(160)},
            'additionalProperties': False}}},
    {'type': 'function', 'function': {'name': 'update_research_state',
        'description': 'Replace a small fallible research map after a material change. Bind observations to exact inspected evidence IDs; retain alternatives and explicitly retire removed nodes. At most three updates in the existing acquisition budget; batch with local review or closure.',
        'parameters': PROPOSAL}}]

GUIDE = '''
Experimental research-loop policy: during acquisition, organize a small, fallible
research map to guide source discovery. This policy permits provisional reasoning;
it never certifies facts, resolves the event, sets probabilities, submits or trades.
After useful material batches, inspect_research_state exposes exact saved-body IDs.
Batch update_research_state with review/assessment/closure when useful. Prefer 3-5
drivers; at most 12 nodes and three lifetime map updates. Do not consume a turn for
unchanged bookkeeping or demand a map before forced closure. Explicit interpretation
corrections use revision_kind=interpretation_correction and consume the same update
cap. Existing search,
source and model caps remain authoritative. A missing map is an explicit limitation.
Separate observations (exact original spans), drivers, assumptions and unknowns.
An evidence binding validates text coordinates only, never meaning, event timing,
causality, independence or truth. Keep supporting AND alternative paths. Relations
are proposed hypotheses, not validated causal edges or conditional probabilities.
Do not multiply marginals or infer target probability from arrows. Do not infer
nonoccurrence from a failed fetch, absent search result or future publication gap.
For source_stated event_time copy a literal phrase from an inspected source span;
its event role remains unverified. Distinguish it from publication and
capture times. Correct earlier interpretations explicitly and retire superseded
nodes; old revisions stay in the journal. Retain conflicting observations as separate
nodes. Primary-rule sources and original exceptions remain authoritative. Material
requests are advisory and cannot replace the immutable acquisition plan or budget.
No target score or suggested forecast belongs in this map. Later Mercury receives
original evidence plus this unverified map and may reject every interpretation.
'''
