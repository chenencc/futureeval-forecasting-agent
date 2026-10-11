"""Source-bound target links: structural coverage, never truth or probabilities."""
import copy
import re
from collections import Counter

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop.schema import TARGET_LINK
from ForecastAgent.runtime.contracts import check_schema

FIELD = 'research_target_logic_policy'
POLICY = 'source_bound_target_links_v1'
GUIDE = '''
Target-linked research: use the frozen target IDs, not invented outcome nodes.
For each useful node supply target_links: target_id, role, effect, reason.
Roles: direct (exact target observation), indicator (leading signal), baseline
(historical comparison), procedure (how the event is decided), context, unknown,
or driver (explicit model hypothesis). An empty list means no target assessment;
it never means the target is covered. reason explains HOW this affects the exact
target and its entity, period, metric, unit, stage and limitations.
Direct requires an observation with applicability=target. A background fact or
expectation cannot be upgraded to a realized target observation. Context and
procedure use effect=context; unknown uses unresolved. Drivers remain hypotheses.
The node applicability enum is target/background/expectation/unknown, NEVER
context, indicator or procedure: those are target-link roles. A missing target
observation uses node kind=unknown, link role=unknown, effect=unresolved, NOT direct.
A quote about guidance is an observation of the guidance (expectation/indicator),
not a driver hypothesis. Keep the hypothesized mechanism in a separate driver.
Only exact supplied R text can establish an observation: a claim in the question,
rules or background is not source evidence. Never insert dates, stitch sentences
with ellipses or paraphrase a headline. If a short exact quote cannot be bound,
record an explicit unknown; no accepted factual map is better than invented text.
Use supports/opposes only with an explicit explanation; missing evidence is not
opposition. For numeric/multiple-choice targets explain which value/range/option
the effect concerns; supports does not mean a binary YES. Do not assign numbers.
Read substantive rows and their headers together. Titles and table separators
are navigation context, not fact nodes. Keep only decision-relevant observations.
Do not invent causal edges to fill a quota: node-to-target links already express
the evidence path. Use relations only for actual dependencies or contradictions.
For targets with only background or indicators, retain uncertainty. Request an
obtainable primary source, contrary evidence or leading indicator where useful;
mark a future realization future_event, not an available missing document.
Target coverage is separate from material receipts. Processing every source or
having no material_requests never establishes adequate evidence or a resolution.
'''


def enabled(bundle):
    policy = bundle.get('request', {}).get(FIELD, 'disabled')
    if policy not in {'disabled', POLICY}:
        raise ValueError('Unknown research target logic policy')
    return policy == POLICY


def targets(bundle):
    """Keep research needs distinct from the optional immutable forecast target."""
    rows = [{'id': 'T' + digest(n['id'])[:12], 'material_need_id': n['id'],
             'kind': 'research_target', 'condition': n['condition'],
             'priority': n['priority'], 'expected_source': n['expected_source'],
             'question_refs': n.get('question_refs', []), 'meaning_verified': False}
            for n in bundle.get('plan') or []]
    policy = bundle.get('request', {}).get('forecast_target_registry_policy', 'disabled')
    if policy not in {'disabled', 'question_and_research_targets_v1'}:
        raise ValueError('Unknown forecast target registry policy')
    if policy == 'question_and_research_targets_v1':
        if not enabled(bundle):
            raise ValueError('Forecast target registry requires target logic')
        request = bundle['request']
        frozen = {k: request.get(k, '') for k in ('id', 'question', 'question_type',
            'resolution_criteria', 'fine_print', 'scaling', 'options')}
        rows.append({'id': 'F' + digest(frozen)[:12], 'material_need_id': None,
            'kind': 'forecast_target', 'condition': request['question'],
            'priority': 'critical', 'expected_source': 'Exact immutable resolution criteria and fine print',
            'question_refs': [], 'meaning_verified': False,
            'instruction': 'The eventual resolving outcome. Research-need coverage does not cover this target. '
                'Link available quantified baselines or predictions as baseline/indicator with scope and limits, '
                'never as direct realized results. Keep unpublished outcomes unresolved.'})
    return rows


def schema(base, *, bundle=None):
    result = copy.deepcopy(base)
    node = result['properties']['nodes']['items']
    node['properties']['target_links'] = {
        'type': 'array', 'maxItems': 4, 'items': copy.deepcopy(TARGET_LINK),
        'description': ('Use exact supplied frozen T research-need and F forecast-target IDs. '
            'T coverage never implies F coverage. Explain scope and limits; [] is unassessed, never covered.'
            if bundle is not None and bundle.get('request', {}).get('forecast_target_registry_policy') == 'question_and_research_targets_v1' else
            'Use exact frozen T IDs. Explain the target-specific effect and limits. [] is unassessed, never covered.')}
    if 'target_links' not in node['required']:
        node['required'].append('target_links')
    return result


def validate_link(link, node, known):
    check_schema(link, TARGET_LINK)
    if link['target_id'] not in known:
        raise ValueError('Unknown frozen target ID')
    if not link['reason'].strip():
        raise ValueError('Target link requires an explicit effect and scope explanation')
    role, effect = link['role'], link['effect']
    kind = node['kind']
    if role == 'direct' and (kind != 'observation' or node.get('applicability') != 'target'):
        raise ValueError('Direct target evidence requires a target-applicable observation')
    if role in {'context', 'procedure'} and effect != 'context':
        raise ValueError('Context and procedure cannot claim directional event evidence')
    if kind == 'unknown' and (role != 'unknown' or effect != 'unresolved'):
        raise ValueError('An unknown node cannot assert support or opposition')
    if role == 'unknown' and (kind != 'unknown' or effect != 'unresolved'):
        raise ValueError('An unknown target link requires an unknown node')
    if kind in {'driver', 'assumption'} and role != 'driver':
        raise ValueError('A model hypothesis must retain the driver role')
    if kind == 'observation' and role == 'driver':
        raise ValueError('A quoted observation is separate from a model driver hypothesis')


def isolate_node(bundle, original):
    """Reject bad annotations independently; never repair the observation."""
    chosen = copy.deepcopy(original)
    if not enabled(bundle) or not isinstance(chosen, dict):
        return chosen, []
    claim = chosen.get('claim', '')
    if chosen.get('kind') == 'observation' and isinstance(claim, str):
        # Narrow syntax check only. Numeric rows, headings in mixed prose and
        # ordinary short facts remain eligible; no topic/relevance classifier.
        lines = [x.strip() for x in claim.splitlines() if x.strip()]
        if lines and all(re.fullmatch(r'[|:\-+\s]+', x) or re.fullmatch(r'#{1,6}\s+.+', x) for x in lines):
            raise ValueError('Navigation-only heading or table separator is not an observation')
    label_errors = []
    # Unrecognized annotations cannot erase a separately valid literal quote.
    # Unknown is conservative isolation, never a semantic synonym conversion.
    fields = {'applicability': {'target','background','expectation','unknown'},
              'event_stage': {'planned','ongoing','completed','not_applicable','unknown'},
              'time_status': {'source_stated','hypothesized','unknown'}}
    for field, allowed in fields.items():
        value = chosen.get(field)
        if field in chosen and (not isinstance(value,str) or value not in allowed):
            chosen[field] = 'unknown'
            if field == 'event_stage': chosen['stage_basis'] = ''
            if field == 'time_status': chosen['event_time'] = ''
            label_errors.append({'section':'node_labels','node_id':chosen.get('id'),
                'field':field,'proposed':value,'accepted':'unknown',
                'error':'Unsupported annotation isolated; quote and target effect still require independent validation.'})
    links = chosen.get('target_links')
    errors = label_errors
    if not isinstance(links, list) or len(links) > 4:
        errors.append({'section': 'target_links', 'node_id': chosen.get('id'),
                       'error': 'Missing or malformed target links; observation retained unassessed'})
        chosen['target_links'] = []
        return chosen, errors
    counts = Counter(x.get('target_id') for x in links if isinstance(x, dict) and isinstance(x.get('target_id'), str))
    known = {t['id'] for t in targets(bundle)}
    kept = []
    for index, link in enumerate(links):
        try:
            validate_link(link, chosen, known)
            if counts[link['target_id']] != 1:
                raise ValueError('Duplicate target link; every occurrence is isolated')
            kept.append(copy.deepcopy(link))
        except (ValueError, TypeError, KeyError) as exc:
            errors.append({'section': 'target_links', 'node_id': chosen.get('id'),
                           'index': index, 'entry_sha256': digest(link), 'error': str(exc)})
    chosen['target_links'] = kept
    return chosen, errors


def audit(bundle, nodes=None, requests=None):
    """Report current retained links; absent or rejected links never cover targets."""
    current = bundle.get('research_loop', {}).get('current') or {}
    nodes = current.get('nodes', []) if nodes is None else nodes
    requests = current.get('material_requests', []) if requests is None else requests
    registry = targets(bundle)
    known = {t['id'] for t in registry}
    links, errors = [], []
    for node in nodes:
        proposed = node.get('target_links', [])
        counts = Counter(x.get('target_id') for x in proposed if isinstance(x, dict) and isinstance(x.get('target_id'), str))
        for entry in proposed:
            try:
                validate_link(entry, node, known)
                if counts[entry['target_id']] != 1:
                    raise ValueError('Duplicate target link')
                links.append({'node_id': node['id'], **copy.deepcopy(entry),
                              'meaning_verified': False})
            except (ValueError, TypeError, KeyError) as exc:
                errors.append({'node_id': node['id'], 'error': str(exc)})
    rows = []
    for target in registry:
        own = [l for l in links if l['target_id'] == target['id']]
        roles = {l['role'] for l in own}
        status = ('direct_evidence_declared' if 'direct' in roles else
                  'indicators_declared' if 'indicator' in roles else
                  'background_only' if roles & {'baseline', 'procedure', 'context'} else
                  'hypotheses_only' if 'driver' in roles else
                  'unresolved' if 'unknown' in roles else 'unassessed')
        ids = {l['node_id'] for l in own}
        linked_requests = [r for r in requests if set(r['node_ids']) & ids]
        from ForecastAgent.research_loop import predictive_focus
        rows.append({**target, 'status': status, 'links': own,
                     'material_request_count': len(linked_requests),
                     'has_obtainable_request': any(predictive_focus.network_candidate(bundle, r) for r in linked_requests),
                     'adequacy_verified': False})
    return {'policy': POLICY, 'enabled': enabled(bundle), 'targets': rows,
            'unassessed_target_ids': [r['id'] for r in rows if r['status'] == 'unassessed'],
            'no_direct_evidence_target_ids': [r['id'] for r in rows if r['status'] != 'direct_evidence_declared'],
            'unlinked_node_ids': [n['id'] for n in nodes if n['id'] not in {l['node_id'] for l in links}],
            'invalid_links': errors, 'target_link_count': len(links),
            'truth_verified': False, 'probability_computed': False,
            'instruction': 'Declared scope and effects are fallible. Direct evidence is not resolution or adequacy. Unassessed targets and no requests never mean covered; source receipts are separate.'}


def brief(bundle):
    """Avoid repeating long conditions and explanations in bounded agent context."""
    report = audit(bundle)
    return {**{k:v for k,v in report.items() if k != 'targets'},
            'targets': [{k:r[k] for k in ('id','status','material_request_count',
                'has_obtainable_request','adequacy_verified')} for r in report['targets']],
            'condition_location': 'The frozen target registry; this summary adds no source text.'}
