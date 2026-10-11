"""Interpret immutable literal fields against targets without rewriting evidence."""
import copy
import json
from collections import Counter

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.runtime.contracts import check_schema
from ForecastAgent.research_loop import source_facets, state, target_logic, reference_map
from ForecastAgent.research_loop.schema import TARGET_LINK

NAME = 'interpret_source_fields'
SYSTEM = '''Interpret the supplied immutable literal field registry against the exact
question, rules and target conditions. Old graph interpretations are not supplied.
The field quotes/values are originals; their proposed roles and associations remain
unverified. Recheck full selected originals before assigning target relevance.
Return one interpret_source_fields call. Select existing fact IDs only. Do not
rewrite facts, invent entities/dates/values, fetch, forecast or assign probabilities.
Select distinct useful substantive facts, not only publication metadata. There is
no quota. A publication date does not establish an event date or historical cohort.
Directory entries are not dated historical membership; a current listing may be
a baseline with that limitation. Earlier same-metric observations may be useful,
but unpublished future values and failed captures never establish nonoccurrence.
Each observation chooses one primary fact plus up to two context facts from the
SAME captured URL/body/view. Other-source comparisons need separate observations;
do not attribute another source's fields to this observation. The program copies
the primary quote unchanged into the native node. Choose a primary quote <=240
characters; retain date/unit/entity context in context_fact_ids. Field associations
remain provisional even when all words are present. Avoid duplicate observations
about identical text or one actor when other useful facts exist.
Use frozen T/F target IDs. Research needs are not the eventual forecast target.
Context/procedure links have effect=context. Direct links require applicability=
target and an actually realized exact target observation. A prediction or guidance
is expectation/indicator; a dated comparable observation is background/baseline.
Explain scope and limits in short link reasons. No unsupported directional effects
from metadata or source absence. Leave unlinked facts unassessed. For an unavailable
target predicate use unknown_targets with the exact target ID and explicit missing
condition; the program makes a separate unknown node, never a negative fact.
Keep limitations<=180, reasons<=240 and revision_reason<=300 characters. These
annotations and target relations remain model hypotheses, not certified truth.
Copy registry_sha256 exactly. Preserve contradictions as separate source facts.
'''


def tool(bundle, packet):
    source_facets.verify_registry(packet)
    if not target_logic.enabled(bundle) or not reference_map.enabled(bundle):
        raise ValueError('Field target interpretation requires native reference and target policies')
    facts = [f['fact_id'] for f in packet['facts']]
    targets = [t['id'] for t in target_logic.targets(bundle)]
    if not facts or not targets:
        raise ValueError('Field target interpretation requires bound facts and frozen targets')
    link = copy.deepcopy(TARGET_LINK)
    link['properties']['target_id']['enum'] = targets
    observation = {'type': 'object', 'additionalProperties': False,
        'properties': {'fact_id': {'type': 'string', 'enum': facts},
            'context_fact_ids': {'type': 'array', 'maxItems': 2, 'items': {'type': 'string', 'enum': facts}},
            'applicability': {'type': 'string', 'enum': ['target', 'background', 'expectation', 'unknown']},
            'target_links': {'type': 'array', 'maxItems': 4, 'items': link},
            'limitation': {'type': 'string', 'maxLength': 180}},
        'required': ['fact_id', 'context_fact_ids', 'applicability', 'target_links', 'limitation']}
    gap = {'type': 'object', 'additionalProperties': False,
        'properties': {'target_id': {'type': 'string', 'enum': targets},
            'missing_condition': {'type': 'string', 'maxLength': 240},
            'reason': {'type': 'string', 'maxLength': 180}},
        'required': ['target_id', 'missing_condition', 'reason']}
    return {'type': 'function', 'function': {'name': NAME,
        'description': 'Select immutable source facts and explain target relevance; no generated source claims.',
        'parameters': {'type': 'object', 'additionalProperties': False,
            'properties': {'registry_sha256': {'type': 'string', 'enum': [packet['registry_sha256']]},
                'observations': {'type': 'array', 'maxItems': 12, 'items': observation},
                'unknown_targets': {'type': 'array', 'maxItems': 8, 'items': gap},
                'revision_reason': {'type': 'string', 'maxLength': 300}},
            'required': ['registry_sha256', 'observations', 'unknown_targets', 'revision_reason']}}}


def messages(bundle, packet):
    source_facets.verify_registry(packet)
    if not packet['facts']:
        raise ValueError('No bound source facts; preserve the gap without a model call')
    # Originals retain full provenance. The model selects stable field IDs;
    # repeated hashes and submission indices stay in the immutable local proof.
    fields = [{k: copy.deepcopy(f[k]) for k in
               ('fact_id', 'quote', 'value', 'role_hypotheses', 'canonical_reference_id', 'role_verified', 'meaning_verified')}
              | {'binding_reference_ids': [b['reference_id'] for b in f['bindings']],
                 **({'coverage_hypotheses': copy.deepcopy(f['coverage_hypotheses'])} if 'coverage_hypotheses' in f else {}),
                 'literal_location_ambiguous': any(b['location_ambiguous'] for b in f['bindings'])}
              for f in packet['facts']]
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': json.dumps({
        'question': {k: copy.deepcopy(bundle['request'].get(k)) for k in
            (*state.RULE_FIELDS, 'question_type', 'options', 'scaling', 'as_of_utc')},
        'target_conditions': target_logic.targets(bundle),
        'registry_sha256': packet['registry_sha256'], 'immutable_source_facts': fields,
        'originals': packet['originals'], 'source_field_coverage_complete': False,
        'scope': 'Originals and literal fields only; no old graph, verified roles or resolved labels.'}, ensure_ascii=False)}]


def _source_scope(fact):
    return {(b.get('url'), b.get('body_sha256'), b.get('coordinate_space'), b.get('view_sha256'))
            for b in fact['bindings']}


def proposal(bundle, packet, answer, map_tool, *, replace_previous_notes=False):
    """Compile selected facts to the existing native map; isolate invalid siblings."""
    if type(replace_previous_notes) is not bool:
        raise ValueError('Explicit note replacement must be boolean')
    source_facets.verify_registry(packet)
    material = state.catalog(bundle)
    for supplied in packet['originals']:
        current = material['spans'].get(supplied['evidence_id'])
        if current is None or any(current.get(k) != supplied.get(k) for k in
                ('url', 'body_sha256', 'start', 'end', 'text', 'coordinate_space', 'view_sha256')):
            raise ValueError('Original field scope no longer matches the eligible native catalog')
    schema = tool(bundle, packet)['function']['parameters']
    loose = copy.deepcopy(schema)
    loose['properties']['observations']['items'] = {}
    loose['properties']['unknown_targets']['items'] = {}
    check_schema(answer, loose)
    if not answer['revision_reason'].strip():
        raise ValueError('Explain the target interpretation')
    facts = {f['fact_id']: f for f in packet['facts']}
    targets = {t['id'] for t in target_logic.targets(bundle)}
    counts = Counter(n.get('fact_id') for n in answer['observations']
                     if isinstance(n, dict) and isinstance(n.get('fact_id'), str))
    nodes, reviews, rejected = [], [], []
    node_schema = map_tool['function']['parameters']['properties']['nodes']['items']
    def base(ident, kind):
        return {'id': ident, 'kind': kind, 'gap_reason': 'none' if kind == 'observation' else 'ambiguous',
            'interpretation': '', 'limitation': '', 'episode_id': '', 'applicability': 'unknown',
            'evidence_ids': [], 'hypothesis': '', 'target_links': []}
    for index, entry in enumerate(answer['observations']):
        ident = entry.get('fact_id') if isinstance(entry, dict) else None
        try:
            check_schema(entry, schema['properties']['observations']['items'])
            if counts[ident] != 1:
                raise ValueError('Duplicate primary field selection')
            contexts = entry['context_fact_ids']
            if len(contexts) != len(set(contexts)) or ident in contexts:
                raise ValueError('Context field IDs must be distinct from the primary')
            fact = facts[ident]
            if any(_source_scope(facts[i]) != _source_scope(fact) for i in contexts):
                raise ValueError('An atomic observation cannot borrow another source version or view')
            refs = list(dict.fromkeys(facts[i]['canonical_reference_id'] for i in [ident] + contexts))
            node = base('O' + ident[1:], 'observation')
            node.update(evidence_ids=refs, interpretation=fact['quote'], limitation=entry['limitation'],
                        applicability=entry['applicability'], target_links=copy.deepcopy(entry['target_links']))
            check_schema(node, node_schema)
            for link in node['target_links']:
                target_logic.validate_link(link, node, targets)
            if len(nodes) >= map_tool['function']['parameters']['properties']['nodes']['maxItems']:
                raise ValueError('Existing native node cap reached; field remains unselected')
            nodes.append(node)
            reviews.append({'node_id': node['id'], 'fact_id': ident, 'context_fact_ids': copy.deepcopy(contexts),
                'selected_reference_ids': refs, 'source_quote_copied_exactly': True, 'meaning_verified': False})
        except (ValueError, KeyError, TypeError) as exc:
            rejected.append({'index': index, 'fact_id': ident, 'error': str(exc), 'meaning_verified': False})
    gap_counts = Counter(g.get('target_id') for g in answer['unknown_targets']
                         if isinstance(g, dict) and isinstance(g.get('target_id'), str))
    for index, entry in enumerate(answer['unknown_targets']):
        try:
            check_schema(entry, schema['properties']['unknown_targets']['items'])
            if gap_counts[entry['target_id']] != 1 or not entry['missing_condition'].strip() or not entry['reason'].strip():
                raise ValueError('Unknown target needs one explicit nonempty missing condition')
            node = base('U' + digest(entry['target_id'])[:24], 'unknown')
            node.update(hypothesis=entry['missing_condition'], limitation=entry['reason'],
                target_links=[{'target_id': entry['target_id'], 'role': 'unknown', 'effect': 'unresolved',
                               'reason': entry['reason']}])
            check_schema(node, node_schema)
            if len(nodes) >= map_tool['function']['parameters']['properties']['nodes']['maxItems']:
                raise ValueError('Existing native node cap reached; target remains unresolved')
            nodes.append(node)
        except (ValueError, KeyError, TypeError) as exc:
            rejected.append({'gap_index': index, 'error': str(exc), 'meaning_verified': False})
    old = (bundle['research_loop'].get('current') or {}).get('nodes', [])
    audit = {'registry_sha256': packet['registry_sha256'], 'field_reviews': reviews, 'rejected': rejected,
        'unselected_fact_ids': [i for i in facts if i not in {r['fact_id'] for r in reviews}],
        'source_field_coverage_complete': False, 'meaning_verified': False, 'probabilities_computed': False,
        'material_receipts_granted': False, 'replace_previous_notes': replace_previous_notes,
        'previous_notes_sha256': digest(old), 'old_notes_declared_false': False}
    repeated = {}
    for row in reviews:
        fact = facts[row['fact_id']]
        key = digest({'quote': fact['quote'], 'scope': sorted(_source_scope(fact), key=repr)})
        repeated.setdefault(key, []).append(row['node_id'])
    audit['repeated_literal_observation_groups'] = [ids for ids in repeated.values() if len(ids) > 1]
    audit['source_independence_verified'] = False
    if not nodes:
        return None, audit
    args = {'expected_revision': bundle['research_loop']['revision'], 'revision_kind': 'interpretation_correction',
        'material_sha256': material['material_sha256'], 'nodes': nodes, 'relations': [],
        'supporting_path': ('Repeated source quotations are not independent confirmation.'
                            if audit['repeated_literal_observation_groups'] else ''),
        'alternative_path': '', 'material_requests': [],
        'retired_node_ids': [n['id'] for n in old if n['id'] not in {x['id'] for x in nodes}] if replace_previous_notes else [],
        'revision_reason': answer['revision_reason'], 'update_mode': 'replace' if replace_previous_notes else 'merge',
        'material_reviews': []}
    allowed = map_tool['function']['parameters']['properties']
    args = {k: v for k, v in args.items() if k in allowed}
    check_schema(args, map_tool['function']['parameters'])
    return args, audit
