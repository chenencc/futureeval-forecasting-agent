"""Forecast evidence review over unchanged originals, with no extra model stage."""
import copy
from collections import defaultdict

from ForecastAgent.analysis.pilot import digest
from ForecastAgent.research_loop import simple_map
from ForecastAgent.runtime.contracts import check_schema

PROTOCOL = 'forecast-map-v3'
REVIEW = {'type': 'object', 'additionalProperties': False, 'required':
    ['source_group_id', 'role', 'node_ids', 'reason'], 'properties': {
        'source_group_id': {'type': 'string', 'maxLength': 40},
        'role': {'type': 'string', 'enum': ['target', 'baseline', 'leading_indicator', 'context', 'irrelevant', 'unknown']},
        'node_ids': {'type': 'array', 'maxItems': 12, 'items': {'type': 'string', 'maxLength': 40}},
        'reason': {'type': 'string', 'maxLength': 180}}}
TOOL = copy.deepcopy(simple_map.TOOL)
TOOL['function']['parameters']['properties']['source_reviews'] = {
    'type': 'array', 'maxItems': 64, 'items': REVIEW}
TOOL['function']['parameters']['required'].append('source_reviews')

SYSTEM = simple_map.SYSTEM + '''

Your job is to retain evidence useful for FORECASTING, not to find the final answer.
A future release or event normally has no final observed outcome yet. Before a
gap-only map, inspect each source for a dated baseline, a recent revision/trend,
a stated plan/schedule, an explicit forecast assumption, and contrary evidence.
For example, an earlier projection for the SAME measure can be useful background
even though the specified future edition is unpublished. Preserve its literal
value, period and assumption separately; do not relabel it as the target release.
Another entity/metric, directory navigation and unrelated historical documents
may be irrelevant. Do not force observations from them. There is still no quota.

Return source_reviews in the SAME tool call. For every supplied source_group_id,
give its most useful role, the IDs of retained nodes actually bound to that source,
and one short reason for use or exclusion. Consider predictive usefulness, not
just whether the target outcome is already known. A gap may coexist with useful
baseline nodes. If nothing useful exists, give concrete exclusion reasons and an
explicit gap. Reviews are fallible and are not passed to the scoring model.
Groups with identical body hashes share captured text; aliases remain visible.
Do not count aliases as independent confirmation. These groups do not establish
which publisher originated the information. Use only the supplied originals.
'''


def inventory(original_view):
    """Group exact captured body hashes, without hiding any original text."""
    groups = defaultdict(lambda: {'source_ids': [], 'evidence_ids': []})
    source_group = {}
    for source in original_view['sources']:
        key = 'S' + digest(source.get('body_sha256') or source['source_id'])[:20]
        source_group[source['source_id']] = key
        groups[key]['source_ids'].append(source['source_id'])
    for span in original_view['evidence']:
        if span.get('evidence_id'):
            groups[source_group[span['source_id']]]['evidence_ids'].append(span['evidence_id'])
    return [{'source_group_id': key, 'source_ids': value['source_ids'],
        'evidence_ids': sorted(set(value['evidence_ids'])),
        'same_body_aliases': len(value['source_ids']) > 1}
        for key, value in sorted(groups.items())]


def bind_observation(original, material, allowed=None):
    """Quarantine unverifiable time metadata, never repair quotations or handles."""
    from ForecastAgent.research_loop.state import bind_node
    node = copy.deepcopy(original)
    isolated = []
    if node['kind'] == 'observation' and node['claim_origin'] == 'source_quote' and node['time_status'] == 'source_stated':
        refs = [material['spans'].get(ref, {}) for ref in node['evidence_ids']]
        if not node['event_time'].strip() or not any(node['event_time'] in ref.get('text', '') for ref in refs):
            isolated = [{'section': 'node_annotations', 'node_id': node['id'],
                'fields': ['event_time', 'time_status'], 'proposed_values':
                    {key: original[key] for key in ('event_time', 'time_status')},
                'error': 'Time annotation is not a literal phrase in a bound span; retained quotation has unknown time.'}]
            node.update(event_time='', time_status='unknown')
    # References, literal quotation, entity-bearing text and schema remain strict.
    # If any core check fails, no sanitized node or field report is accepted.
    return bind_node(node, material, allowed), node, isolated


def review_audit(reviews, groups, nodes):
    """Validate review coordinates only; never reject nodes for a bad review."""
    expected = {g['source_group_id']: g for g in groups}
    bound = {n['id']: set(n.get('evidence_ids', [])) for n in nodes}
    accepted, rejected, seen = [], [], set()
    if not isinstance(reviews, list):
        reviews = []
        rejected.append({'index': None, 'error': 'Source review list unavailable'})
    repeated = {key for key in expected if sum(isinstance(r, dict) and r.get('source_group_id') == key for r in reviews) > 1}
    for index, review in enumerate(reviews):
        try:
            check_schema(review, REVIEW)
            key = review['source_group_id']
            if key not in expected or key in repeated:
                raise ValueError('Unknown or repeated source group')
            if not review['reason'].strip() or len(set(review['node_ids'])) != len(review['node_ids']):
                raise ValueError('Empty reason or duplicate node ID')
            if any(n not in bound or not (bound[n] & set(expected[key]['evidence_ids'])) for n in review['node_ids']):
                raise ValueError('Review node is absent or not bound to this visible source')
            accepted.append(copy.deepcopy(review)); seen.add(key)
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({'index': index, 'entry_sha256': digest(review), 'error': str(exc)})
    used = {n for r in accepted for n in r['node_ids']}
    return {'schema': 'forecast-source-review-v1', 'reviews': accepted, 'rejected': rejected,
        'missing_source_group_ids': sorted(set(expected) - seen),
        'unreviewed_observation_ids': [n['id'] for n in nodes if n['kind'] == 'observation' and n['id'] not in used],
        'meaning_verified': False, 'acceptance_gate': False, 'new_model_calls': 0,
        'originals_unchanged': True,
        'instruction': 'Reviews explain selection; they do not certify relevance, completeness or truth.'}
