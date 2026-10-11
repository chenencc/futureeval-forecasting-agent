"""Interpret immutable literal fields against targets without rewriting evidence."""
import copy
import json
import re
from collections import Counter

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.runtime.contracts import check_schema
from ForecastAgent.research_loop import source_facets, state, target_logic, reference_map
from ForecastAgent.research_loop.schema import TARGET_LINK

NAME = 'interpret_source_fields'
QUALIFIED_GUIDE = '''
Qualified source attribution: each link declares source_premise_fact_ids,
rule_context_target_ids, scope_basis and scope_assumptions. Source premises must
be selected primary/context fields of this same observation. Rule definitions
are separate T/F conditions, never a source snapshot. scope_basis=source_only
requires no rule contexts or assumptions; numerical premises must occur in the
selected source quotes. A comparison with a rule threshold uses rule_context,
not source_only. Explicit arithmetic or scope bridges use assumption.
Rule_context requires declared rule IDs and no assumptions; numbers may come
from those exact definitions, but are labelled as rule context, not observed facts.
Assumption requires at least one explicit scope_assumptions string. For example,
mapping an undated current listing to an older dated cohort is an assumption,
not a source observation. The compiler places each assumption in a separate
unverified hypothesis node. Its inferred effect belongs there; the original
observation supplies context only. Do not invent a source date to avoid a gap.
Keep qualified reason<=180, observation limitation<=155 and gap reason<=165
characters. Raw annotations remain archived; the compiler labels model reasons
and limitations explicitly. A passing numeric-origin check does not verify date
roles, entity associations or semantic truth. No probabilities or CPTs.
'''
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


def tool(bundle, packet, *, qualified=False):
    if type(qualified) is not bool:
        raise ValueError('Qualified attribution mode must be boolean')
    source_facets.verify_registry(packet)
    if not target_logic.enabled(bundle) or not reference_map.enabled(bundle):
        raise ValueError('Field target interpretation requires native reference and target policies')
    facts = [f['fact_id'] for f in packet['facts']]
    targets = [t['id'] for t in target_logic.targets(bundle)]
    if not facts or not targets:
        raise ValueError('Field target interpretation requires bound facts and frozen targets')
    link = copy.deepcopy(TARGET_LINK)
    link['properties']['target_id']['enum'] = targets
    if qualified:
        link['properties'].update(
            source_premise_fact_ids={'type':'array','minItems':1,'maxItems':3,'items':{'type':'string','enum':facts}},
            rule_context_target_ids={'type':'array','maxItems':4,'items':{'type':'string','enum':targets}},
            scope_basis={'type':'string','enum':['source_only','rule_context','assumption']},
            scope_assumptions={'type':'array','maxItems':2,'items':{'type':'string','maxLength':180}})
        link['required'] = list(link['properties'])
        link['properties']['reason']['maxLength'] = 180
    observation = {'type': 'object', 'additionalProperties': False,
        'properties': {'fact_id': {'type': 'string', 'enum': facts},
            'context_fact_ids': {'type': 'array', 'maxItems': 2, 'items': {'type': 'string', 'enum': facts}},
            'applicability': {'type': 'string', 'enum': ['target', 'background', 'expectation', 'unknown']},
            'target_links': {'type': 'array', 'maxItems': 4, 'items': link},
            'limitation': {'type': 'string', 'maxLength': 180}},
        'required': ['fact_id', 'context_fact_ids', 'applicability', 'target_links', 'limitation']}
    if qualified:
        observation['properties']['limitation']['maxLength'] = 155
    gap = {'type': 'object', 'additionalProperties': False,
        'properties': {'target_id': {'type': 'string', 'enum': targets},
            'missing_condition': {'type': 'string', 'maxLength': 240},
            'reason': {'type': 'string', 'maxLength': 180}},
        'required': ['target_id', 'missing_condition', 'reason']}
    if qualified:
        gap['properties']['reason']['maxLength'] = 165
    return {'type': 'function', 'function': {'name': NAME,
        'description': 'Select immutable source facts and explain target relevance; no generated source claims.',
        'parameters': {'type': 'object', 'additionalProperties': False,
            'properties': {'registry_sha256': {'type': 'string', 'enum': [packet['registry_sha256']]},
                'observations': {'type': 'array', 'maxItems': 12, 'items': observation},
                'unknown_targets': {'type': 'array', 'maxItems': 8, 'items': gap},
                'revision_reason': {'type': 'string', 'maxLength': 300}},
            'required': ['registry_sha256', 'observations', 'unknown_targets', 'revision_reason']}}}


def messages(bundle, packet, *, qualified=False):
    if type(qualified) is not bool:
        raise ValueError('Qualified attribution mode must be boolean')
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
    return [{'role': 'system', 'content': SYSTEM + (QUALIFIED_GUIDE if qualified else '')}, {'role': 'user', 'content': json.dumps({
        'question': {k: copy.deepcopy(bundle['request'].get(k)) for k in
            (*state.RULE_FIELDS, 'question_type', 'options', 'scaling', 'as_of_utc')},
        'target_conditions': target_logic.targets(bundle),
        'registry_sha256': packet['registry_sha256'], 'immutable_source_facts': fields,
        'originals': packet['originals'], 'source_field_coverage_complete': False,
        'scope': 'Originals and literal fields only; no old graph, verified roles or resolved labels.'}, ensure_ascii=False)}]


def _source_scope(fact):
    return {(b.get('url'), b.get('body_sha256'), b.get('coordinate_space'), b.get('view_sha256'))
            for b in fact['bindings']}


def qualified_link(link, selected_fact_ids, facts, target_rows):
    """Check declared premise origins, never certify a field relationship."""
    source_ids = link['source_premise_fact_ids']
    rule_ids = link['rule_context_target_ids']
    assumptions = link['scope_assumptions']
    if (len(source_ids) != len(set(source_ids)) or not set(source_ids) <= set(selected_fact_ids)
            or len(rule_ids) != len(set(rule_ids)) or not set(rule_ids) <= set(target_rows)):
        raise ValueError('Source premises or rule contexts are outside the declared observation scope')
    basis = link['scope_basis']
    if basis == 'source_only' and (rule_ids or assumptions):
        raise ValueError('Source-only attribution cannot contain rule definitions or assumptions')
    if basis == 'rule_context' and (not rule_ids or assumptions):
        raise ValueError('Rule-context attribution requires definitions without a hidden assumption')
    if basis == 'assumption' and (not assumptions or any(not s.strip() for s in assumptions)):
        raise ValueError('A scope bridge needs an explicit nonempty assumption')
    source_text = '\n'.join(facts[i]['quote'] for i in source_ids)
    rule_text = '\n'.join(target_rows[i]['condition'] for i in rule_ids)
    origins = []
    for literal in sorted(set(re.findall(r'(?<![\w])\d+(?:[.,]\d+)*(?:%|\b)', link['reason']))):
        pattern = r'(?<![\w])' + re.escape(literal) + r'(?![\w])'
        in_source, in_rules = bool(re.search(pattern,source_text)), bool(re.search(pattern,rule_text))
        origins.append({'literal':literal,'in_selected_source_quotes':in_source,
                        'in_declared_rule_conditions':in_rules,'meaning_verified':False})
    if basis == 'source_only' and any(not o['in_selected_source_quotes'] for o in origins):
        raise ValueError('Source-only explanation contains an unbound numeric premise; classify rule context or an explicit assumption')
    if basis == 'rule_context' and any(not (o['in_selected_source_quotes'] or o['in_declared_rule_conditions']) for o in origins):
        raise ValueError('Rule-context explanation contains an unbound numerical inference')
    prefix = {'source_only':'Model source relation: ', 'rule_context':'Model rule-context relation: ',
              'assumption':'Model conditional relation: '}[basis]
    native = {k:copy.deepcopy(link[k]) for k in ('target_id','role','effect')}
    native['reason'] = prefix + link['reason']
    if basis == 'assumption':
        native.update(role='context',effect='context',
            reason='Selected original fields supply context; the proposed scope bridge remains an explicit model assumption.')
    return native, {'scope_basis':basis,'source_premise_fact_ids':source_ids,
        'rule_context_target_ids':rule_ids,'scope_assumptions':copy.deepcopy(assumptions),
        'received_reason':link['reason'],'numeric_origins':origins,'meaning_verified':False}


def proposal(bundle, packet, answer, map_tool, *, replace_previous_notes=False, qualified=False):
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
    schema = tool(bundle, packet, qualified=qualified)['function']['parameters']
    loose = copy.deepcopy(schema)
    loose['properties']['observations']['items'] = {}
    loose['properties']['unknown_targets']['items'] = {}
    check_schema(answer, loose)
    if not answer['revision_reason'].strip():
        raise ValueError('Explain the target interpretation')
    facts = {f['fact_id']: f for f in packet['facts']}
    target_rows = {t['id']:t for t in target_logic.targets(bundle)}
    targets = set(target_rows)
    counts = Counter(n.get('fact_id') for n in answer['observations']
                     if isinstance(n, dict) and isinstance(n.get('fact_id'), str))
    nodes, reviews, rejected, relations = [], [], [], []
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
            if not reference_map.substantive(fact['quote']):
                raise ValueError('Primary field is a heading/table separator, not a substantive original observation')
            if any(_source_scope(facts[i]) != _source_scope(fact) for i in contexts):
                raise ValueError('An atomic observation cannot borrow another source version or view')
            refs = list(dict.fromkeys(facts[i]['canonical_reference_id'] for i in [ident] + contexts))
            node = base('O' + ident[1:], 'observation')
            node.update(evidence_ids=refs, interpretation=fact['quote'], limitation=entry['limitation'],
                        applicability=entry['applicability'], target_links=copy.deepcopy(entry['target_links']))
            attribution, annotation_errors, additions, edges = [], [], [], []
            if qualified:
                node['limitation'] = 'Model limitation: ' + entry['limitation'] if entry['limitation'] else ''
                links = []
                link_counts = Counter(l['target_id'] for l in entry['target_links'])
                for position, link in enumerate(entry['target_links']):
                    try:
                        if link_counts[link['target_id']] != 1:
                            raise ValueError('One qualified relation per target and observation')
                        normalized, proof = qualified_link(link,[ident]+contexts,facts,target_rows)
                        target_logic.validate_link(normalized,node,targets)
                        hypotheses, dependencies = [], []
                        if link['scope_basis'] == 'assumption':
                            for assumption in link['scope_assumptions']:
                                h = base('A'+digest([ident,link['target_id'],assumption])[:24],'assumption')
                                h.update(gap_reason='ambiguous',hypothesis=assumption,
                                    limitation='Model scope bridge; not established by the selected literal fields.',
                                    target_links=[{'target_id':link['target_id'],'role':'driver','effect':link['effect'],
                                        'reason':'Unverified scope assumption: '+link['reason']}])
                                check_schema(h,node_schema)
                                target_logic.validate_link(h['target_links'][0],h,targets)
                                hypotheses.append(h)
                                dependencies.append({'from_id':node['id'],'to_id':h['id'],'kind':'evidential',
                                    'rationale':'Selected literal fields inform an unverified scope bridge.',
                                    'mechanism':'','rule_quote':''})
                        if len(nodes)+1+len(additions)+len(hypotheses)>map_tool['function']['parameters']['properties']['nodes']['maxItems']:
                            raise ValueError('Assumption and original observation must fit the existing native node cap together')
                        links.append(normalized);attribution.append(proof);additions.extend(hypotheses);edges.extend(dependencies)
                    except (ValueError,KeyError,TypeError) as exc:
                        annotation_errors.append({'link_index':position,'received_link':copy.deepcopy(link),'error':str(exc)})
                node['target_links'] = links
                if entry['target_links'] and not links:
                    node['applicability'] = 'unknown'
            check_schema(node, node_schema)
            for link in node['target_links']:
                target_logic.validate_link(link, node, targets)
            if len(nodes) >= map_tool['function']['parameters']['properties']['nodes']['maxItems']:
                raise ValueError('Existing native node cap reached; field remains unselected')
            nodes.append(node)
            nodes.extend(additions);relations.extend(edges)
            reviews.append({'node_id': node['id'], 'fact_id': ident, 'context_fact_ids': copy.deepcopy(contexts),
                'selected_reference_ids': refs, 'source_quote_copied_exactly': True, 'meaning_verified': False})
            if qualified:
                reviews[-1].update(qualified_attribution=attribution,annotation_rejections=annotation_errors,
                    received_limitation=entry['limitation'])
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
            if qualified:
                node['limitation']='Model gap: '+entry['reason']
                node['target_links'][0]['reason']='Model gap: '+entry['reason']
            check_schema(node, node_schema)
            if len(nodes) >= map_tool['function']['parameters']['properties']['nodes']['maxItems']:
                raise ValueError('Existing native node cap reached; target remains unresolved')
            nodes.append(node)
        except (ValueError, KeyError, TypeError) as exc:
            rejected.append({'gap_index': index, 'error': str(exc), 'meaning_verified': False})
    old = (bundle['research_loop'].get('current') or {}).get('nodes', [])
    primary_ids = {r['fact_id'] for r in reviews}
    context_ids = {i for r in reviews for i in r['context_fact_ids']}
    audit = {'registry_sha256': packet['registry_sha256'], 'field_reviews': reviews, 'rejected': rejected,
        'selected_primary_fact_ids': sorted(primary_ids), 'selected_context_fact_ids': sorted(context_ids),
        'unselected_fact_ids': [i for i in facts if i not in primary_ids | context_ids],
        'source_field_coverage_complete': False, 'meaning_verified': False, 'probabilities_computed': False,
        'material_receipts_granted': False, 'replace_previous_notes': replace_previous_notes,
        'previous_notes_sha256': digest(old), 'old_notes_declared_false': False}
    if qualified:
        audit.update(qualified_attribution_policy='explicit_source_rule_assumption_v1',
            source_role_meaning_verified=False,received_unknown_targets=copy.deepcopy(answer['unknown_targets']))
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
        'material_sha256': material['material_sha256'], 'nodes': nodes, 'relations': relations,
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
