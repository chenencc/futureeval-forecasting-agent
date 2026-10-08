"""Audited response compatibility over immutable source numbers.

Only identifiers and explicit unit scales are normalized. Period and accounting
claims stay model interpretations. Wrong arithmetic is never corrected silently.
"""
import copy
import math
import re

from ForecastAgent.market_pulse import facts, financial_chain as chain

UNIT_SUPPORT = {
    'USD_millions': r'\bmillions?\b|\$\s*\d[\d,]*(?:\.\d+)?\s*[Mm]\b',
    'USD_billions': r'\bbillions?\b|\$\s*\d[\d,]*(?:\.\d+)?\s*[Bb]\b',
    'shares_millions': r'\bmillions?\b', 'shares_thousands': r'\bthousands?\b',
    'USD': r'\$|USD|dollars', 'USD_per_share': r'per.share|EPS|\$',
    'shares': r'\bshares\b', 'percent': r'%|percent',
}


def unit(value, name):
    if name in {'fraction', 'dimensionless'}:
        return chain.finite(value), name, 1
    if name not in facts.SCALES:
        raise ValueError('Unsupported declared unit: ' + str(name))
    scale = .01 if name == 'percent' else facts.SCALES[name]
    kind = ('fraction' if name == 'percent' else 'shares' if name.startswith('shares')
            else 'USD_per_share' if name == 'USD_per_share' else 'USD')
    return chain.finite(value) * scale, kind, scale


def bind(bundle, table, selections, *, minimum_facts=4):
    """Bind exact visible tokens; retain period claims separately from quotations."""
    if minimum_facts not in range(1, 5) or not minimum_facts <= len(selections) <= 9:
        raise ValueError(f'{minimum_facts} to nine selected facts required')
    tokens = {n['token_id']: (row, n) for row in table['candidates'] for n in row['numbers']}
    bound, audit, aliases = [], [], {}
    for i, item in enumerate(selections, 1):
        token_id = item['token_id']
        if token_id not in tokens:
            if token_id != f'V{i}' or item['unit_ref'] not in tokens:
                raise ValueError('Unknown or ambiguous numeric token alias')
            token_id = item['unit_ref']
            audit.append({'kind': 'token_alias', 'from': item['token_id'], 'to': token_id})
        row, number = tokens[token_id]
        if item['metric'] not in facts.METRICS or item['source_unit'] not in UNIT_SUPPORT:
            raise ValueError('Unknown metric or financial unit')
        if item['metric'] == 'diluted_eps' and item['source_unit'] != 'USD_per_share':
            raise ValueError('EPS must be a per-share quantity')
        if item['metric'] == 'diluted_shares' and not item['source_unit'].startswith('shares'):
            raise ValueError('Diluted shares must be a share quantity')
        if item['metric'] in {'revenue', 'net_income', 'operating_income', 'expense'} and not item['source_unit'].startswith('USD'):
            raise ValueError('Currency metric cannot use share/rate units')
        refs = copy.deepcopy(row['refs'])
        # Body tables can have their unit caption outside the short row context.
        body = bundle['pages'][row['url']]['content']
        captions = []
        for m in re.finditer(r'[^\n]{0,160}\b[Ii]n (?:millions|billions|thousands)\b[^\n]{0,160}', body):
            captions.append(facts.original_ref(row['url'], None, body, m.start(), m.end()))
        origin = row['refs'][0]['start'] if row['document_index'] is None else 0
        captions.sort(key=lambda r: (r['start'] > origin, abs(r['start'] - origin)))
        refs.extend(captions[:2])
        # Preserve the actual table header separately from a model's period label.
        if row['document_index'] is None:
            headers = [m for m in re.finditer(r'(?:Three|Six|Nine|Twelve) Months Ended[^\n]*', body, re.I) if m.start() <= origin]
            if headers:
                m = headers[-1]
                refs.append(facts.original_ref(row['url'], None, body, m.start(), min(m.start()+700, origin)))
        local_ids = {r.get('ref_id') for r in refs if r.get('ref_id')}
        if item['unit_ref'] not in local_ids | {token_id, row['record_id']}:
            raise ValueError('Unknown or foreign-source unit reference')
        supported = [r for r in refs if re.search(UNIT_SUPPORT[item['source_unit']], r['quote'], re.I)]
        if not supported:
            raise ValueError('Declared unit has no original literal support')
        preferred = next((r for r in supported if r.get('ref_id') == item['unit_ref']), supported[0])
        if preferred.get('ref_id') != item['unit_ref']:
            audit.append({'kind': 'unit_reference_binding', 'supplied': item['unit_ref'],
                          'original_reference': preferred})
        refs_by_id = {r.get('ref_id'): r for r in refs if r.get('ref_id')}
        period_id = item['period_ref']
        if period_id not in refs_by_id:
            if period_id not in {row['record_id'], token_id}:
                raise ValueError('Unknown or foreign-source period reference')
            audit.append({'kind': 'record_reference_alias', 'from': period_id, 'to': row['refs'][0]['ref_id']})
        claim = item['period_text']
        if not isinstance(claim, str) or not claim.strip():
            raise ValueError('Missing period interpretation')
        if item['basis'] not in {'GAAP','adjusted_or_nonGAAP','unknown','not_applicable'} or item['role'] not in {'actual','management_guidance','estimate','other_context'}:
            raise ValueError('Unknown accounting basis or source role')
        value, kind, factor = unit(number['value'], item['source_unit'])
        fid = f'V{i}'
        aliases.setdefault(token_id, []).append(fid)
        bound.append({'fact_id': fid, 'token_id': token_id, 'metric': item['metric'],
            'basis': item['basis'], 'role': item['role'], 'source_unit': item['source_unit'],
            'raw_value': number['value'], 'normalized_value': value, 'normalized_unit': kind,
            'conversion_factor': factor, 'numeric_token': number,
            'original_row_ref': row['refs'][0], 'unit_original_ref': preferred,
            'period_claim': claim, 'period_original_refs': refs,
            'period_claim_is_verbatim': any(claim in r['quote'] for r in refs),
            'semantic_interpretation_verified': False, 'numeric_binding_valid': True,
            'source_url': row['url']})
    return bound, aliases, audit


def compile_derivation(report, bound, financial, aliases=None):
    """All equations use canonical dollars, shares and fractional rates."""
    aliases = aliases or {}
    known = {f['fact_id']: f['normalized_value'] for f in bound}
    units = {f['fact_id']: f['normalized_unit'] for f in bound}
    declared_units = {f['fact_id']:f.get('source_unit',f['normalized_unit']) for f in bound}
    rationales = {}
    assumptions, arithmetic, repairs = [], [], []
    for a in report['assumptions']:
        if not re.fullmatch(r'A\d+', a['id']) or a['id'] in known or not a['rationale'].strip():
            raise ValueError('Invalid assumption identifier or rationale')
        refs = []
        for ref in a['fact_refs']:
            if ref not in known:
                targets = aliases.get(ref, [])
                if len(targets) != 1:
                    raise ValueError('Unknown or ambiguous assumption fact reference')
                repairs.append({'kind': 'assumption_reference_alias', 'from': ref, 'to': targets[0]})
                ref = targets[0]
            if ref not in {f['fact_id'] for f in bound}:
                raise ValueError('Assumption cannot cite another assumption as a fact')
            refs.append(ref)
        value, kind, factor = unit(a['value'], a['unit'])
        assumptions.append({**a, 'fact_refs': refs, 'normalized_value': value,
                            'normalized_unit': kind, 'conversion_factor': factor})
        known[a['id']], units[a['id']] = value, kind
        declared_units[a['id']] = a['unit'];rationales[a['id']] = a['rationale']
    for c in report['calculations']:
        if not re.fullmatch(r'C\d+', c['id']) or c['id'] in known or any(r not in known for r in c['inputs']):
            raise ValueError('Calculation inputs must be named facts, assumptions or earlier calculations')
        expected_unit = chain.calculation_unit(c['operation'], [units[r] for r in c['inputs']])
        claimed, declared_unit, factor = unit(c['model_value'], c['unit'])
        if declared_unit != expected_unit:
            raise ValueError('Calculation output dimension mismatch: ' + c['id'])
        actual = chain.calculate(c['operation'], [known[r] for r in c['inputs']])
        conversion = None
        # An explicit scale conversion preserves physical quantity. Recognize
        # only a complete named identity: e.g. 62.5 billion * 1000 = 62500 million.
        if c['operation']=='product' and len(c['inputs'])==2:
            dimensional = [r for r in c['inputs'] if units[r] not in {'fraction','dimensionless'}]
            constants = [r for r in c['inputs'] if units[r]=='dimensionless']
            if len(dimensional)==len(constants)==1:
                source,constant = dimensional[0],constants[0]
                source_name = declared_units[source]
                if source_name in facts.SCALES and c['unit'] in facts.SCALES and source_name!=c['unit']:
                    ratio = facts.SCALES[source_name]/facts.SCALES[c['unit']]
                    explicit = re.search(r'\b(?:convert|conversion)\b',rationales.get(constant,''),re.I)
                    if explicit and math.isclose(known[constant],ratio,rel_tol=1e-12) and math.isclose(known[source],claimed,rel_tol=1e-12,abs_tol=1e-12):
                        actual=known[source]
                        conversion={'kind':'explicit_unit_conversion_identity','calculation_id':c['id'],
                            'from_unit':source_name,'to_unit':c['unit'],'declared_factor':ratio,
                            'canonical_quantity_unchanged':True}
                        repairs.append(conversion)
        if not math.isclose(actual, claimed, rel_tol=.005, abs_tol=.0005):
            raise ValueError(f"Arithmetic mismatch {c['id']}: program={actual}, model={claimed}")
        known[c['id']], units[c['id']] = actual, expected_unit
        declared_units[c['id']]=c['unit']
        arithmetic.append({**c, 'program_value': actual, 'normalized_unit': expected_unit,
            'normalized_model_value': claimed, 'conversion_factor': factor, 'arithmetic_valid': True})
        if conversion:arithmetic[-1]['canonical_operation']='unit_conversion_identity'
    forecast = report['forecast']
    expected = 'USD_per_share' if financial['metric'] == 'gaap_diluted_eps' else 'USD'
    if forecast['unit'] != expected:
        raise ValueError('Forecast must use question units')
    values = [chain.finite(forecast[k]) for k in ('p10','p50','p90')]
    if not values[0] < values[1] < values[2]:
        raise ValueError('Invalid or zero-width uncertainty interval')
    central = forecast['central_calculation_ref']
    if central not in {c['id'] for c in arithmetic} or units[central] != expected or not math.isclose(known[central], values[1], rel_tol=.005, abs_tol=.0005):
        raise ValueError('Forecast median is not bound to a valid equation')
    if not isinstance(report['thesis'], str) or len(report['thesis']) > 2200:
        raise ValueError('Invalid or oversized thesis')
    return {'facts': bound, 'assumptions': assumptions, 'calculations': arithmetic,
        'forecast': forecast, **{k: report[k] for k in ('thesis','limitations','downside','upside')},
        'numeric_binding_valid': True, 'arithmetic_valid': True,
        'interpretation_truth_verified': False, 'compatibility_repairs': repairs}
