"""Explicit document requirements and exhaustive review coverage, not verdicts."""
import re

PROTOCOL = 'material-coverage-v2'


def contract(need):
    text = need.get('condition', '')
    forms = sorted(set(re.findall(r'\b(?:S-[1348]|10-[KQ]|8-K|20-F|6-K)\b', text, re.I)))
    reporting = bool(re.search(r'\b(?:news|reporting|reports? about|coverage)\b', text, re.I))
    order = bool(re.search(r'\b(?:court order|injunction)\b', text, re.I)) and not reporting
    original = order and bool(re.search(r'\b(?:official|original|full|complete|copy of)\b',text,re.I))
    return {'required_form_identifiers': forms, 'original_court_document_required':original,
            'requires_staying_or_vacating_effect':order and bool(re.search(r'\b(?:vacates?|stays?|enjoins?|staying|vacating)\b',text,re.I)),
            'context_is_retained': True, 'event_truth_is_not_material_capture': True}


def issues(need, binding):
    spec = contract(need)
    quote = binding.get('quote', '')
    result = []
    normalize = lambda s: re.sub(r'[\u2010-\u2015\u2212]', '-', s)
    for form in spec['required_form_identifiers']:
        if not re.search(r'\b'+re.escape(form)+r'\b', normalize(quote), re.I):
            result.append('required_form_identifier_unobserved')
            break
    if spec['original_court_document_required']:
        context = binding.get('document_context', '')
        # A report describing an order is useful context, but not the order.
        # Hostname or .pdf alone never establishes an original court document.
        header = re.search(r'\b(?:UNITED STATES|U\.S\.)\s+(?:DISTRICT|SUPREME|COURT OF APPEALS)\s+COURT\b|\bSUPREME COURT OF THE UNITED STATES\b', context, re.I)
        disposition = re.search(r'\b(?:ORDER|OPINION|JUDGMENT|MEMORANDUM)\b', context, re.I)
        if not header or not disposition:
            result.append('original_court_document_unverified')
    if spec['requires_staying_or_vacating_effect']:
        denial=re.search(r'\b(?:denied|denies|rejected|rejects)\b[^.\n]{0,150}\b(?:stay|injunction|motion)\b',quote,re.I)
        positive=re.search(r'\b(?:granted|grants|vacated|vacates|enjoined|enjoins|stayed)\b',quote,re.I)
        if denial and not positive:
            result.append('contrary_only_order_effect_witness')
    return result


def validate_coverage(payload, assessments, bindings):
    """Every need is explicitly assessed; missing is unknown, never absent."""
    allowed = {n['id'] for n in payload['needs']}
    passages = {p['passage_id'] for p in payload['passages']}
    if not isinstance(assessments, list) or len(assessments) != len(allowed):
        raise ValueError('need_coverage_incomplete')
    seen = set()
    for row in assessments:
        if not isinstance(row, dict) or set(row) != {'need_id','status','passage_ids','reason'}:
            raise ValueError('need_coverage_invalid_shape')
        if row['need_id'] not in allowed or row['need_id'] in seen:
            raise ValueError('need_coverage_duplicate_or_unknown')
        seen.add(row['need_id'])
        if row['status'] not in {'proposed_binding','no_matching_passage','uncertain'}:
            raise ValueError('need_coverage_invalid_status')
        ids = row['passage_ids']
        if not isinstance(ids, list) or len(ids)>3 or any(not isinstance(p,str) or p not in passages for p in ids):
            raise ValueError('need_coverage_unknown_passage')
        if not isinstance(row['reason'], str) or not 1<=len(row['reason'])<=240:
            raise ValueError('need_coverage_missing_reason')
        if row['status'] != 'uncertain' and not ids:
            raise ValueError('need_coverage_missing_witness')
        related = [b for b in bindings if b['need_id']==row['need_id']]
        if row['status']=='proposed_binding' and not related:
            raise ValueError('need_coverage_binding_missing')
        if row['status']=='proposed_binding' and any(b.get('passage_id') not in ids for b in related):
            raise ValueError('need_coverage_binding_witness_mismatch')
        if row['status']!='proposed_binding' and related:
            raise ValueError('need_coverage_binding_conflict')
    return assessments


def decorate(payload):
    from ForecastAgent.supplement import binding_guard
    return {**payload, 'coverage_protocol':PROTOCOL,
            'needs':[{**n, 'witness_contract':contract(n),
                      'required_axes':binding_guard.required_axes(n),
                      'source_requirement':binding_guard.source_requirement(n)} for n in payload['needs']]}
