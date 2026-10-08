"""Saved financial reports -> explicit period inventory -> exposure gaps.

No issuer, question ID, fiscal date or financial value is built into this module.
Quarter-end calendar dates never establish a fiscal quarter. Unknown periods and
unparsed tables remain gaps, rather than acquiring invented fiscal labels.
"""
import copy
import hashlib
import re

from ForecastAgent.market_pulse import analysis, derivation, facts, formulas, review
from ForecastAgent.market_pulse.financial import issuer_profile
from ForecastAgent.analysis.pilot import digest

VERSION = 'financial-saved-source-coverage-v1'
ORDINAL = r'(?:first|second|third|fourth)'
LABEL = re.compile(
    rf'\bQ[1-4]\s*FY\s*20\d{{2}}\b|\bFY\s*(?:20)?\d{{2}}\s*Q[1-4]\b|'
    rf'\bfiscal\s+(?:year\s+)?20\d{{2}}\s+{ORDINAL}\s+quarter\b|'
    rf'\b{ORDINAL}\s+quarter\s+(?:of\s+)?20\d{{2}}\b', re.I)
DISCLOSURE = re.compile(
    r'one[- ]time|non[- ]cash|refund|adjusted|non[- ]GAAP|excluding|'
    r'severance|legal (?:cost|charge|settlement)|percentage points', re.I)


def explicit_labels(text):
    """Retain original label offsets; comparisons do not date current actuals."""
    links = [m.span() for m in re.finditer(r'https?://\S+', text)]
    out = []
    for m in LABEL.finditer(text):
        if any(a <= m.start() < b for a, b in links):
            continue
        prefix = text[max(0, m.start() - 100):m.start()]
        if re.search(r'(?:compared (?:with|to)|versus)\s*(?:the\s*)?$', prefix, re.I):
            continue
        label = m.group()
        match = re.fullmatch(r'FY\s*((?:20)?\d{2})\s*Q([1-4])', label, re.I)
        if match:
            year = int(match[1]); year = 2000 + year if year < 100 else year
            period = (int(match[2]), year)
        else:
            period = derivation.fiscal_period(re.sub(r'fiscal\s+year', 'fiscal', label, flags=re.I))
        if period:
            out.append({'period': list(period), 'quote': label, 'start': m.start(), 'end': m.end(),
                'fiscal_label_explicit': bool(re.search(r'FY|fiscal', label, re.I))})
    return out


def ordinal(period):
    return period[1] * 4 + period[0]


def inventory(bundle):
    financial = analysis.contract(bundle['request'])
    target = derivation.fiscal_period(financial['target_period'])
    if not target:
        raise ValueError('Explicit target fiscal quarter required for source coverage')
    view, exclusions = analysis.analysis_view(bundle)
    profile = issuer_profile(bundle['request']); issuer = profile['issuer_label']
    sources = []
    for url, page in view['pages'].items():
        text = page['content']; intro = text[:4000]
        labels = explicit_labels(intro)
        # A body label without a report identity is a lead, not an issuer fact.
        identity = bool(issuer and re.search(r'\b' + re.escape(issuer) + r'\b', intro, re.I))
        report = bool(re.search(r'financial results|earnings release|quarter.*results|'
            r'quarter.*(?:financial highlights|earnings conference call)', intro, re.I | re.S))
        observations = []
        for label in labels:
            section = intro[max(0, label['start'] - 80):label['end'] + 100]
            if re.search(r'guidance|outlook|we expect|expected to', section, re.I):
                continue
            line_start = intro.rfind('\n', 0, label['start']) + 1
            prefix = intro[line_start:label['start']]
            suffix = intro[label['end']:label['end'] + 120]
            # Ordinary comparative metric sentences cannot date the report.
            # Accept explicit fiscal prose or release/highlights/call headings.
            heading = (re.search(r'earnings release|reports?\s*$', prefix, re.I)
                or re.match(r'\s*(?:results|financial highlights|earnings conference call)', suffix, re.I)
                or (not prefix.strip() and re.match(r'\s*(?:\n|$)', suffix)))
            if not label['fiscal_label_explicit'] and not heading:
                continue
            observations.append(label)
        periods = {tuple(v['period']) for v in observations}
        period = next(iter(periods)) if identity and report and len(periods) == 1 else None
        refs = [facts.original_ref(url, None, text, v['start'], v['end']) for v in observations]
        sources.append({'url': url, 'content_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'body_chars': len(text), 'captured_at_utc': page.get('retrieved_at_utc'),
            'reported_period': list(period) if period else None, 'period_refs': refs,
            'issuer_literal_present': identity, 'report_context_present': report,
            'period_status': 'single_original_report_label' if period else 'unresolved_or_ambiguous',
            'publication_time_verified': False, 'semantic_interpretation_verified': False})
    return {'schema': VERSION, 'target_period': list(target), 'issuer': issuer,
        'sources': sources, 'excluded_sources': exclusions,
        'calendar_to_fiscal_mapping_inferred': False, 'provider_calls': 0}


def _exposed(ref, library):
    return any(r['url'] == ref['url'] and r.get('document_index') == ref.get('document_index')
        and r.get('field') == ref.get('field') and r['text_sha256'] == ref['text_sha256']
        and r['start'] <= ref['start'] <= ref['end'] <= r['end'] for r in library)


def audit(bundle, variables, library=None):
    formulas.validate_variables(bundle, variables)
    if library is None:
        library = review.context_catalog(bundle, variables)['original_evidence_library']
    inv = inventory(bundle); target = tuple(inv['target_period'])
    primary_metric = 'diluted_eps' if analysis.contract(bundle['request'])['metric'] == 'gaap_diluted_eps' else 'revenue'
    prior = [tuple(s['reported_period']) for s in inv['sources'] if s['reported_period']
        and ordinal(s['reported_period']) < ordinal(target)]
    latest = max(prior, key=ordinal) if prior else None
    slots = []
    previous = (target[0] - 1, target[1]) if target[0] > 1 else (4, target[1] - 1)
    for name, period in [('latest_identified_saved_actual', latest),
                         ('immediately_preceding_quarter_actual', previous),
                         ('same_quarter_prior_year_actual', (target[0], target[1] - 1))]:
        sources = [s for s in inv['sources'] if period and s['reported_period'] == list(period)]
        vs = [v for v in variables if v['metric'] == primary_metric and v['role'] == 'actual'
            and (derivation.period(v) == period if period else False)]
        source_view = any(_exposed(r, library) for s in sources for r in s['period_refs'])
        fact_view = bool(vs) and all(_exposed(v['original_row_ref'], library) for v in vs)
        status = ('covered' if fact_view else 'fact_not_exposed' if vs else
            'saved_source_without_period_bound_fact' if sources else 'not_established_from_saved_sources')
        slots.append({'role': name, 'metric': primary_metric,
            'period': list(period) if period else None, 'status': status,
            'saved_source_urls': [s['url'] for s in sources], 'fact_ids': [v['fact_id'] for v in vs],
            'original_report_label_exposed': source_view, 'fact_rows_exposed': fact_view})
    guides = [v for v in variables if v['metric'] == 'revenue' and v['role'] == 'management_guidance'
        and derivation.guidance_applies(v, target)]
    slots.append({'role': 'target_revenue_guidance', 'period': list(target),
        'status': 'covered' if guides and all(_exposed(v['original_row_ref'], library) for v in guides)
            else 'not_established_from_saved_sources', 'fact_ids': [v['fact_id'] for v in guides],
        'absence_is_not_negative_evidence': True})
    return {**inv, 'slots': slots,
        'all_saved_pages_have_explicit_periods': all(s['reported_period'] for s in inv['sources']),
        'future_target_actual_required': False, 'coverage_is_not_factual_verification': True,
        'variables_sha256': digest(variables), 'model_exposure_sha256': digest(library)}


def extraction_packet(bundle, variables):
    """Expose only saved actual sources missing a target-relevant period fact."""
    coverage = audit(bundle, variables)
    urls = set(u for slot in coverage['slots']
        if slot['status'] == 'saved_source_without_period_bound_fact' for u in slot['saved_source_urls'])
    table = facts.discover(bundle); facts.validate_references(bundle, table)
    saved_candidates = [r for r in table['candidates'] if r['url'] in urls]
    # A shared report label cannot prove which quarter/YTD table column a token
    # belongs to. Existing reviewed table facts remain valid inputs; new table
    # tokens require a separate explicit coordinate-review stage.
    candidates = [r for r in saved_candidates if r['document_index'] is None and '|' not in r['row']]
    withheld_rows = [{'record_id': r['record_id'], 'url': r['url'],
        'reason': 'new_table_token_requires_explicit_quarter_column_review'}
        for r in saved_candidates if r not in candidates]
    # Never silently truncate to one convenient row/source. Oversized discovery
    # remains a declared gap and requires an explicit later review stage.
    if len(candidates) > 60:
        raise ValueError('Missing-source candidate bound exceeded; split a new explicit stage')
    refs, rows = {}, []
    for row in candidates:
        for ref in row['refs']:
            key = (ref['url'], ref['document_index'], ref['start'], ref['end'])
            if key not in refs:
                refs[key] = {k: v for k, v in ref.items() if k != 'ref_id'}
                refs[key]['ref_ids'] = []
            refs[key]['ref_ids'].append(ref['ref_id'])
        rows.append({k: row[k] for k in ('record_id', 'url', 'document_index', 'row', 'numbers')})
        rows[-1]['context_ref_ids'] = [r['ref_id'] for r in row['refs']]
    contexts = []
    for s in coverage['sources']:
        if s['url'] not in urls:
            continue
        text = bundle['pages'][s['url']]['content']
        # Uncut introduction plus every local adjustment/disclosure paragraph.
        spans = [(0, min(3500, len(text)))]
        for m in DISCLOSURE.finditer(text):
            a = text.rfind('\n\n', 0, m.start()) + 2
            b = text.find('\n\n', m.end()); b = len(text) if b < 0 else b
            if b - a <= 2400:
                spans.append((a, b))
        for a, b in sorted(set(spans)):
            contexts.append(facts.original_ref(s['url'], None, text, a, b))
    return {'coverage': coverage, 'financial_rows': rows,
        'reference_library': list(refs.values()), 'disclosure_context': contexts,
        'allowed_source_periods': {s['url']: s['reported_period'] for s in coverage['sources'] if s['url'] in urls},
        'input_variables_sha256': digest(variables), 'withheld_table_rows': withheld_rows,
        'omitted_candidate_rows': len(withheld_rows),
        'source_text_untrusted': True}, {'candidates': candidates}


def append_bound(bundle, variables, table, selections, packet):
    """Append literal-bound model interpretations; never rewrite earlier facts."""
    from ForecastAgent.market_pulse.numeric_binding import bind
    if not selections:
        return copy.deepcopy(variables), [], []
    visible_tokens = {n['token_id'] for row in packet['financial_rows'] for n in row['numbers']}
    if any(s['token_id'] not in visible_tokens for s in selections):
        raise ValueError('Supplement token is not an admitted narrative token')
    bound, _, compatibility = bind(bundle, table, selections, minimum_facts=1)
    target = tuple(packet['coverage']['target_period']); ids = []
    result = copy.deepcopy(variables)
    next_id = max((int(v['fact_id'][1:]) for v in variables), default=0) + 1
    seen = {(v['original_row_ref']['url'], v['original_row_ref']['document_index'],
        v['original_row_ref']['start'] + v['numeric_token']['start'], v['metric']) for v in variables}
    for v in bound:
        ref = v['original_row_ref']; period = derivation.period(v)
        supplied = next(s for s in selections if s['token_id'] == v['token_id'])
        literal = next((r for r in v['period_original_refs'] if r.get('ref_id') == supplied['period_ref']), None)
        if not literal or supplied['period_text'] not in literal['quote']:
            raise ValueError('Supplement requires a literal original report label')
        if not period or list(period) != packet['allowed_source_periods'].get(ref['url']) or ordinal(period) >= ordinal(target):
            raise ValueError('Supplement period differs from saved prior-report label')
        if v['role'] not in {'actual', 'other_context'}:
            raise ValueError('Actual-report supplement cannot relabel guidance or estimates')
        signature = (ref['url'], ref['document_index'], ref['start'] + v['numeric_token']['start'], v['metric'])
        if signature in seen:
            raise ValueError('Duplicate original-token supplement')
        seen.add(signature)
        v['fact_id'] = f'V{next_id}'; next_id += 1; ids.append(v['fact_id'])
        v.update(review_method='Bounded saved-report extraction; literal numeric/unit/period binding.',
            admission_status='provisional_source_bound_interpretation',
            interpretation_independently_verified=False)
        result.append(v)
    formulas.validate_variables(bundle, result)
    if digest(result[:len(variables)]) != digest(variables):
        raise ValueError('Existing financial facts changed')
    return result, ids, compatibility
