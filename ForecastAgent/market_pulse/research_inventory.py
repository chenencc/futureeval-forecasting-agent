"""Additive P0 research coverage. Saved leads never become verified facts."""
import re
from collections import Counter
from ForecastAgent.market_pulse import analysis, consensus, derivation, formulas, guidance, source_coverage
from ForecastAgent.analysis.pilot import digest
from ForecastAgent.market_pulse.financial import issuer_profile
from ForecastAgent.market_pulse.research_identity import profile as research_profile

VERSION = 'financial-research-inventory-v1'
LEADS = {
    'target_guidance': r'guidance|outlook|we expect',
    'target_consensus': r'consensus|analyst.{0,40}estimate',
    'consensus_revisions': r'estimate.{0,40}revis|revis.{0,40}estimate',
    'operating_observations': r'deliveries|deployment|backlog|bookings|subscriber|unit sales',
    'accounting_changes': r'one[- ]time|tax|diluted shares|refund|non[- ]cash|restructur',
}


def checked_library(bundle, library):
    for ref in library:
        if ref.get('field'):
            text = bundle['request'].get(ref['field'], '')
        else:
            page = bundle['pages'][ref['url']]
            index = ref.get('document_index')
            text = page['content'] if index is None else page['documents'][index - 1]['page_content']
        if digest_text(text) != ref['text_sha256'] or not 0 <= ref['start'] <= ref['end'] <= len(text):
            raise ValueError('Exposure reference does not match saved original text')
        if ref.get('original_text', ref.get('quote', text[ref['start']:ref['end']])) != text[ref['start']:ref['end']]:
            raise ValueError('Exposure excerpt changed')


def digest_text(text):
    return consensus.sha(text)


def fact_exposed(variable, library):
    refs = [variable['original_row_ref'], variable['unit_original_ref'], *variable['period_original_refs']]
    return all(source_coverage._exposed(ref, library) for ref in refs)


def audit(bundle, variables, *, library=None, consensus_packages=(), as_of=None):
    formulas.validate_variables(bundle, variables)
    actual_exposure = library is not None
    library = library or []
    checked_library(bundle, library)
    try:
        contract = analysis.contract(bundle['request'])
        target = list(derivation.fiscal_period(contract['target_period']))
        history = source_coverage.audit(bundle, variables, library=library)
        metric = 'diluted_eps' if contract['metric'] == 'gaap_diluted_eps' else 'revenue'
        basis = 'GAAP' if metric == 'diluted_eps' else 'not_applicable'
        unit = 'USD_per_share' if metric == 'diluted_eps' else 'USD'
    except ValueError as exc:
        try:
            contract = guidance.contract(bundle['request'])
            contract['issuer'] = research_profile(bundle['request'])['issuer_label']
            target = list(derivation.fiscal_period(contract['target_guidance_period']))
            metric = contract['metric']; basis = 'not_applicable'; unit = contract['normalized_unit']
            history = {'slots': [], 'sources': [], 'diagnostic': 'Historical actual contract not applicable to guidance leaf'}
        except ValueError:
            raise ValueError('Research contract unsupported: ' + str(exc)) from exc
    slots = []
    if not history['slots']:
        slots.extend({'role': role, 'status': 'not_applicable', 'reason': history['diagnostic'],
            'exposure_observed': actual_exposure} for role in ('latest_identified_saved_actual',
                'immediately_preceding_quarter_actual', 'same_quarter_prior_year_actual'))
    for old in history['slots'][:3]:
        selected = [v for v in variables if v['fact_id'] in old['fact_ids']]
        status = ('exposed' if selected and all(fact_exposed(v, library) for v in selected) and actual_exposure else
            'parsed_unexposed' if old['fact_ids'] else 'saved_unparsed' if old['saved_source_urls'] else 'not_found')
        slots.append({**old, 'status': status, 'exposure_observed': actual_exposure,
            'latest_publicly_available_established': False})
    for role, pattern in LEADS.items():
        leads = []
        for url, page in bundle.get('pages', {}).items():
            text = page.get('content', '')
            for match in list(re.finditer(pattern, text, re.I))[:12]:
                a, b = max(0, match.start() - 100), min(len(text), match.end() + 180)
                leads.append({'url': url, 'start': a, 'end': b, 'quote': text[a:b],
                    'text_sha256': digest_text(text), 'target_applicability_verified': False})
        selected = []
        if role == 'target_guidance':
            selected = [v for v in variables if v['role'] == 'management_guidance'
                and v['metric'] == {'revenue_guidance': 'revenue', 'gross_margin_guidance': 'gross_margin',
                    'operating_expense_guidance': 'operating_expenses'}.get(metric, metric)
                and derivation.guidance_applies(v, tuple(target))]
        elif role == 'accounting_changes':
            selected = [v for v in variables if v['role'] == 'other_context'
                and v['metric'] in {'tax_rate', 'diluted_shares', 'net_income', 'tax_expense'}]
        exposed = bool(selected) and actual_exposure and all(fact_exposed(v, library) for v in selected)
        slots.append({'role': role, 'status': 'exposed' if exposed else 'parsed_unexposed' if selected else 'saved_unparsed' if leads else 'not_found',
            'fact_ids': [v['fact_id'] for v in selected], 'leads': leads,
            'absence_is_not_negative_evidence': True, 'exposure_observed': actual_exposure})
    records, assessments = [], []
    for package in consensus_packages:
        consensus.validate(package)
        records.extend(package['records'])
    records = list({r['record_id']: r for r in records}.values())
    if records and not as_of:
        raise ValueError('Consensus inventory requires an explicit UTC as-of time')
    for record in records:
        assessments.append(consensus.assess(record, {'issuer': contract['issuer'], 'metric': metric,
            'basis': basis, 'normalized_unit': unit, 'fiscal_period': target}, as_of=as_of))
    eligible = [r for r, a in zip(records, assessments) if a['compatible'] and a['as_of_eligible']
        and r['statistic'] in {'mean', 'median', 'low', 'high'}]
    slot = next(s for s in slots if s['role'] == 'target_consensus')
    if eligible:
        slot.update(status='parsed_unexposed', record_ids=[r['record_id'] for r in eligible],
            exposure_observed=False, publication_time_verified=False)
    revision_summary = consensus.revisions(eligible)
    if revision_summary['changes']:
        next(s for s in slots if s['role'] == 'consensus_revisions').update(
            status='parsed_unexposed', changes=revision_summary['changes'], exposure_observed=False)
    return {'schema': VERSION, 'question_id': str(bundle['request'].get('id')),
        'issuer': contract['issuer'], 'target_period': target, 'metric': metric,
        'slots': slots, 'state_distribution': dict(Counter(s['status'] for s in slots)),
        'source_inventory': history, 'consensus_assessments': assessments,
        'consensus_lineage': consensus.lineage(eligible),
        'consensus_revisions': revision_summary,
        'input_bundle_sha256': digest(bundle), 'variables_sha256': digest(variables),
        'model_exposure_sha256': digest(library), 'model_exposure_supplied': actual_exposure,
        'future_target_actual_required': False, 'coverage_is_not_factual_verification': True,
        'provider_calls': 0, 'forecasts_changed': False}
