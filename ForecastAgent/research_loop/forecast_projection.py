"""Opt-in removal of named acquisition metadata from forecast map delivery.

This is a delivery projection, not a graph update or semantic repair. Every
observation, relationship, source binding, target condition and material gap
remains literal. Unknown extension fields stay visible by default.
"""
import copy
from ForecastAgent.analysis.pilot import digest

LEGACY = 'legacy'
POLICY = 'forecast-bound-map-metadata-v1'


def validate(policy):
    if policy not in {LEGACY, POLICY}:
        raise ValueError('Unknown forecast map projection')
    return policy


def project(notes, *, policy=LEGACY):
    validate(policy)
    if not isinstance(notes, dict):
        raise ValueError('Forecast map must be an object')
    result = copy.deepcopy(notes)
    omitted = []

    def remove(obj, keys, path):
        for key in keys:
            if key in obj:
                value = obj.pop(key)
                omitted.append({'path': path + [key], 'value': value,
                                'sha256': digest(value)})

    if policy == POLICY:
        # The durable native map protocol is not an evidence property.
        remove(result, ('map_protocol',), [])
        coverage = result.get('target_coverage')
        if coverage is not None:
            if not isinstance(coverage, dict):
                raise ValueError('Target coverage must be an object')
            # Keep condition, status, unknown/contrary IDs, roles and warnings.
            # These counters and policy switches belong to the execution audit.
            remove(coverage, ('policy', 'enabled', 'target_link_count',
                              'probability_computed', 'link_location'), ['target_coverage'])
            for index, target in enumerate(coverage.get('targets', [])):
                if not isinstance(target, dict):
                    raise ValueError('Forecast target must be an object')
                remove(target, ('material_request_count', 'has_obtainable_request'),
                       ['target_coverage', 'targets', index])
        for index, need in enumerate(result.get('material_requests', [])):
            if not isinstance(need, dict):
                raise ValueError('Material request must be an object')
            # Availability and information_state are deliberately retained:
            # future realization differs from an obtainable missing baseline.
            remove(need, ('suggested_tool',), ['material_requests', index])
    audit = {'policy': policy, 'native_notes_sha256': digest(notes),
             'forecast_notes_sha256': digest(result), 'omitted': omitted,
             'original_text_or_graph_updated': False,
             'semantic_fields_rewritten': False, 'unknown_fields_retained': True,
             'new_model_http': 0, 'prediction_gain_proven': False}
    return result, audit
