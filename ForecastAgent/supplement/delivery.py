"""Total per-need material handoff without converting uncertainty to truth."""


def build(ledger, journal):
    assessments = {}
    rejections = {}
    reading_coverage={}
    for review in journal.get('material_reviews', []):
        result = review.get('result', {})
        for row in result.get('need_assessments', []):
            assessments[row['need_id']] = row
            rejections[row['need_id']]=[]
        reading_coverage.update(result.get('reading_coverage',{}))
        for row in result.get('rejected_records', []):
            ident = row.get('record', {}).get('need_id') if isinstance(row.get('record'),dict) else None
            if ident:
                rejections.setdefault(ident, []).append(row['reason'])
    rows = []
    for need in ledger['needs']:
        ident = need['id']
        captured = need['target_material_captured']
        assessment = assessments.get(ident)
        reasons = sorted(set(rejections.get(ident, []) +
            [code for b in need.get('blocked_material_bindings', []) for code in b['guard']['issues']]))
        if not captured and not reasons:
            reasons = (list(ledger.get('review_errors', [])) if assessment is None else []) or [
                'not_assessed' if assessment is None else
                'no_matching_passage' if assessment['status']=='no_matching_passage' else 'material_fit_unverified']
        rows.append({'need_id':ident, 'condition':need['condition'],
            'status':'material_captured' if captured else
                'review_failed' if (ledger.get('review_errors') and assessment is None) or rejections.get(ident) else 'recorded_gap',
            'reason_codes':reasons, 'model_assessment':assessment,
            'bindings':need.get('material_bindings', []),
            'blocked_bindings':need.get('blocked_material_bindings', []),
            'reading_coverage':reading_coverage.get(ident),
            'saved_candidates':need.get('candidates', []), 'truth_verified':False})
    ready = bool(rows) and all(n['status']=='material_captured' for n in rows)
    return {'schema':'material-delivery-v1','needs':rows,
        'state':'materials_ready' if ready else 'completed_with_recorded_gaps',
        'all_needs_exported':True, 'automatic_resubmit':False,
        'evidence_retained':True, 'semantic_completeness_verified':False,
        'review_errors':ledger.get('review_errors', [])}
