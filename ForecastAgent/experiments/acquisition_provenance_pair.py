"""Frozen V2/V3 review comparison; reuse plans and never reset provider budgets."""
from ForecastAgent.supplement import acquisition_contract as base
from ForecastAgent.supplement import acquisition_ids as ids
from ForecastAgent.supplement import acquisition_provenance as v3


def agent_review(bundle, execute, checkpoint, *, max_chars=60000):
    plan = bundle['frozen_plan']
    packet = base.reading_packet(bundle['pages'], max_chars=max_chars)
    arms = {}
    for arm in bundle['arm_order']:
        def save(value):
            checkpoint({**value, 'phase': arm + '-' + value['phase']})
        if arm == 'v3':
            arms[arm] = v3.review_saved(bundle, plan, execute, save, max_chars=max_chars)
        else:
            payload = {'question': bundle['request'], 'needs': plan['needs'], 'reading': packet,
                       'selected_character_budget': max_chars}
            reply = execute('annotate_v2_baseline', ids.REVIEW_PROMPT, payload, ids.tools()[1])
            save({'phase': 'annotation_reply_saved', 'reply': reply})
            rows, repairs = ids.envelope(reply, 'annotations')
            review = ids.bind_review(plan, packet, rows, bundle['pages'])
            result = {'requirements': plan, 'reading': packet, 'review': review,
                      'coverage': ids.coverage(plan, review), 'compatibility_repairs': repairs}
            result['application_status'] = ids.application_status(result)
            arms[arm] = result
            save({'phase': 'ledger_saved', 'result': result})
    # Combined rows are only for transport-level accounting, never fusion.
    review = {k: sum((a['review'][k] for a in arms.values()), [])
              for k in ('annotations', 'rejected_annotations', 'uncovered_assessments')}
    return {'schema': 'acquisition-provenance-paired-review', 'arms': arms,
        'requirements': plan, 'reading': packet, 'review': review, 'coverage': [],
        'logical_model_decisions': 2, 'semantic_completeness_verified': False,
        'application_status': 'reviewed_with_gaps' if all(a['application_status'] == 'reviewed_with_gaps'
            for a in arms.values()) else 'partial_review'}
