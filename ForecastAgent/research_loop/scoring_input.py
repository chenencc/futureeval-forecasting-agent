"""Research-only boundary between acquisition execution and forecast inputs.

Keep original platform fields literally. Execution hashes, tool policies and
allowances remain in the parent archive, not in the model-facing question.
Unknown fields are retained: this adapter never guesses that new metadata is
irrelevant. No source, quote, rule, probability or physical ledger is rewritten.
"""
import copy
from ForecastAgent.analysis.pilot import digest

POLICY = 'native-acquisition-scoring-boundary-v1'
EXECUTION_FIELDS = frozenset({
    'capability_code_identity', 'capability_policy', 'predictive_information_contract',
    'collection_model', 'collection_purpose', 'pipeline', 'acquisition_profile',
    'acquisition_focus', 'acquisition_strategy', 'drain_unseen_reads_before_stall',
    'source_reading_policy', 'source_recovery_policy_sha256', 'recover_sources_before_stall',
    'exa_search_policy', 'research_dispatch_limits', 'research_state_policy',
    'research_acquisition_policy', 'research_grounding_policy', 'research_update_policy',
    'research_delivery_policy', 'research_reading_policy', 'research_gap_policy',
    'research_reference_map_policy', 'research_target_logic_policy',
    'research_dispatch_policy', 'post_supplement_map_stage', 'post_supplement_map_policy',
    'forecast_target_registry_policy'})


def project(bundle):
    """Return a distinct common scoring parent and an exact omitted-field audit."""
    if not isinstance(bundle.get('request'), dict):
        raise ValueError('Native request object required')
    request = bundle['request']
    for field in ('id', 'question', 'resolution_criteria', 'question_type'):
        if not isinstance(request.get(field), str) or not request[field].strip():
            raise ValueError('Full platform forecast contract required: ' + field)
    result = copy.deepcopy(bundle)
    removed = {k: result['request'].pop(k) for k in sorted(EXECUTION_FIELDS) if k in result['request']}
    if any(result['request'][k] != value for k, value in request.items() if k not in removed):
        raise ValueError('Forecast contract changed')
    if any(result[k] != value for k, value in bundle.items() if k != 'request'):
        raise ValueError('Original material or cumulative ledger changed')
    audit = {'policy': POLICY, 'native_request_sha256': digest(request),
        'forecast_request_sha256': digest(result['request']),
        'omitted_execution_field_sha256': {k: digest(v) for k, v in removed.items()},
        'retained_fields': sorted(result['request']), 'question_rules_changed': False,
        'originals_changed': False, 'new_http': 0, 'analysis_core_changed': False,
        'scope': 'Research adapter only; not wired into production.'}
    return result, audit
