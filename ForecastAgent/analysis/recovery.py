"""Salvage structurally usable model outputs without claiming citation validity."""
import copy
import json
from ForecastAgent.providers.decisions import probability


def recover(message, packet, error):
    calls = message.get('tool_calls', [])
    if len(calls) != 1 or calls[0].get('function', {}).get('name') != 'record_analysis':
        return None
    try:
        candidate = json.loads(calls[0]['function']['arguments'])
        p = probability(candidate['reasoning_probability_yes'])
    except (ValueError, TypeError, KeyError):
        return None
    if not isinstance(candidate.get('conditions'), list) or not candidate['conditions']:
        return None
    library = {e['evidence_id']: e for e in packet['evidence']}
    conditions = []
    quarantine = []
    for index, condition in enumerate(candidate['conditions']):
        if not isinstance(condition, dict) or not isinstance(condition.get('requirement'), str):
            return None
        supplied = condition.get('evidence_refs', [])
        supplied = supplied if isinstance(supplied, list) else []
        valid = list(dict.fromkeys(ref for ref in supplied if isinstance(ref, str) and ref in library))
        quarantine.extend({'section': 'condition', 'index': index, 'reference': ref}
                          for ref in supplied if not isinstance(ref, str) or ref not in library)
        conditions.append({'condition_id': f'C{index + 1}', 'requirement': condition['requirement'],
                           'evidence_refs': valid, 'coverage': 'unknown', 'status': 'uncertain',
                           'observation_window': str(condition.get('observation_window', 'Unknown')),
                           'gap': 'Original structured analysis failed validation; condition interpretation is withheld.'})
    # Keep source spans for reading, never unvalidated factual interpretations.
    used = list(dict.fromkeys(ref for condition in conditions for ref in condition['evidence_refs']))
    for index, fact in enumerate(candidate.get('facts', []) if isinstance(candidate.get('facts'), list) else []):
        refs = fact.get('evidence_refs', []) if isinstance(fact, dict) else []
        refs = refs if isinstance(refs, list) else []
        for ref in refs:
            if isinstance(ref, str) and ref in library:
                if ref not in used:
                    used.append(ref)
            else:
                quarantine.append({'section': 'fact', 'index': index, 'reference': ref})
        if not refs:
            quarantine.append({'section': 'fact', 'index': index, 'reason': 'Missing evidence references'})
    gaps = ['Structured validation failure: ' + str(error),
            'Recovered numeric probability is provisional; unsupported narratives and factual interpretations are withheld.']
    report = {key: 'Withheld after structured validation failure.' for key in
              ('rule_decomposition', 'event_tree', 'base_rate', 'case_for_yes', 'case_for_no',
               'contradictions', 'source_independence', 'temporal_leakage')}
    report.update(conditions=conditions, facts=[], gaps=gaps, reasoning_probability_yes=p)
    # No dummy fact is created to pass the ordinary parser.
    report['facts'] = [{'claim': library[ref]['text'], 'evidence_refs': [ref], 'supports': 'context'} for ref in used[:12]]
    audit = {'schema': 'analysis_recovery_v1', 'status': 'provisional',
             'original_candidate': copy.deepcopy(candidate), 'validation_error': str(error),
             'quarantined_references': quarantine, 'narratives_withheld': True,
             'probability_origin': 'original_model_output', 'no_new_provider_calls': True,
             'source_entailment_verified': False}
    return report, audit


def task_result(ident, routes=None, error=None):
    """Always record an outcome; operational defaults are never model predictions."""
    if routes is not None:
        p = routes['routes']['reasoning_only']['probability_yes']
        mercury = routes['routes']['reasoning_then_mercury']['probability_yes']
        provisional = bool(routes.get('quality', {}).get('recovery_applied'))
        return {'schema': 'analysis_task_result_v1', 'id': ident,
                'status': 'provisional' if provisional else 'partial' if mercury is None else 'completed',
                'reasoning_probability_yes': p, 'mercury_probability_yes': mercury,
                'equal_mean_probability_yes': routes['equal_mean_probability_yes'],
                'operational_probability_yes': routes['equal_mean_probability_yes'] if mercury is not None else p,
                'operational_origin': 'available_model_output', 'error': error,
                'automated_use_eligible': False}
    blocked = bool(error and any(term in str(error).lower() for term in ('hash', 'integrity', 'identity', 'changed', 'mismatch', 'invalid capture path')))
    return {'schema': 'analysis_task_result_v1', 'id': ident,
            'status': 'blocked_integrity' if blocked else 'unavailable',
            'reasoning_probability_yes': None, 'mercury_probability_yes': None,
            'equal_mean_probability_yes': None,
            'operational_probability_yes': None if blocked else .5,
            'operational_origin': None if blocked else 'uninformed_workflow_default',
            'error': str(error), 'automated_use_eligible': False}
