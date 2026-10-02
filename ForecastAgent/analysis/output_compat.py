"""Conservative, auditable transport normalization for structured analyses."""
import copy
import json


def normalize(message):
    result = copy.deepcopy(message)
    calls = result.get('tool_calls', [])
    changes = []
    if len(calls) != 1 or calls[0].get('function', {}).get('name') != 'record_analysis':
        return result, changes
    try:
        report = json.loads(calls[0]['function']['arguments'])
    except (ValueError, KeyError, TypeError):
        return result, changes
    if not isinstance(report, dict):
        return result, changes
    if isinstance(report.get('gaps'), str):
        original = report['gaps']
        report['gaps'] = [original]
        changes.append({'path': 'gaps', 'before': original, 'after': report['gaps'],
                        'reason': 'Wrap a scalar gap without modifying its text.'})
    conditions = report.get('conditions')
    for index, condition in enumerate(conditions if isinstance(conditions, list) else []):
        if isinstance(condition, dict) and condition.get('status') == 'unknown':
            condition['status'] = 'uncertain'
            changes.append({'path': f'conditions.{index}.status', 'before': 'unknown',
                            'after': 'uncertain', 'reason': 'Canonicalize a non-affirmative status.'})
    if changes:
        calls[0]['function']['arguments'] = json.dumps(report)
    return result, changes
