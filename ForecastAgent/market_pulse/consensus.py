"""Offline, source-bound consensus imports. No network or forecast calls.

Mappings are explicit interpretations, not independently verified facts.
Fiscal labels, upstream identity and publication dates are never inferred.
"""
import hashlib
import json
import math
import re
from datetime import datetime, timezone

VERSION = 'financial-consensus-v1'
UNITS = {'USD': 1, 'USD_millions': 1e6, 'USD_billions': 1e9,
         'USD_per_share': 1, 'percent': 1, 'count': 1, 'GWh': 1}
STATISTICS = {'mean', 'median', 'low', 'high', 'standard_deviation', 'analyst_count'}


def sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def number(value):
    text = str(value).strip().replace(',', '').replace('\u2212', '-')
    if re.fullmatch(r'\(\d+(?:\.\d+)?\)', text):
        text = '-' + text[1:-1]
    if not re.fullmatch(r'-?\d+(?:\.\d+)?', text):
        raise ValueError('Cell is not an unambiguous numeric literal')
    result = float(text)
    if not math.isfinite(result):
        raise ValueError('Non-finite numeric value')
    return result


def html_tables(html):
    """Retain complete rectangular grids; ambiguous merged tables are withheld."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    grids, gaps = [], []
    for index, table in enumerate(soup.find_all('table')):
        rows = []
        try:
            for row in table.find_all('tr'):
                if row.find_parent('table') != table:
                    continue
                cells = row.find_all(['td', 'th'], recursive=False)
                if any(int(c.get('colspan', 1)) != 1 or int(c.get('rowspan', 1)) != 1 for c in cells):
                    raise ValueError('Merged cells require an explicit table review')
                if cells:
                    rows.append([c.get_text(' ', strip=True) for c in cells])
            if not rows or len(rows) > 5000 or len(rows[0]) > 100:
                raise ValueError('Empty or oversized table')
            if any(len(row) != len(rows[0]) for row in rows):
                raise ValueError('Non-rectangular table requires explicit review')
            grids.append({'table_index': index, 'rows': rows})
        except (ValueError, TypeError) as exc:
            gaps.append({'table_index': index, 'reason': str(exc)})
    return grids, gaps


def import_grid(rows, metadata, mappings, *, raw_source=None):
    """Map exact row/column labels. Roles must distinguish actuals from estimates.

    Each mapping names row, column, expected_row_label, expected_column_label,
    metric, basis, source_unit, statistic, period_kind and period_label.
    Optional fiscal_period is [quarter, year] and requires an explicit FY label.
    """
    canonical = json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
    source_hash = sha(raw_source if raw_source is not None else canonical)
    required = ('url', 'issuer', 'captured_at_utc')
    if any(not metadata.get(k) for k in required):
        raise ValueError('URL, issuer and capture time are required')
    if not rows or any(len(r) != len(rows[0]) for r in rows):
        raise ValueError('A complete rectangular grid is required')
    records, gaps = [], []
    for mapping in mappings:
        try:
            r, c = mapping['row'], mapping['column']
            if type(r) is not int or type(c) is not int or r < 1 or c < 1:
                raise ValueError('Data coordinates must exclude row and column headers')
            if rows[r][0] != mapping['expected_row_label'] or rows[0][c] != mapping['expected_column_label']:
                raise ValueError('Original row or column label changed')
            if mapping['role'] != 'consensus_estimate':
                raise ValueError('Actual or unspecified roles are not consensus records')
            statistic = mapping['statistic']; unit = mapping['source_unit']
            statistic_ref = None
            if 'statistic_header_row' in mapping:
                header_row = mapping['statistic_header_row']
                if type(header_row) is not int or header_row < 0 or rows[header_row][c] != mapping['expected_statistic_label']:
                    raise ValueError('Original statistic header changed')
                statistic_ref = {'row': header_row, 'column': c, 'text': rows[header_row][c]}
            if statistic not in STATISTICS or unit not in UNITS:
                raise ValueError('Unsupported statistic or unit')
            if mapping['basis'] not in {'GAAP', 'adjusted_or_nonGAAP', 'unknown', 'not_applicable'}:
                raise ValueError('Unsupported accounting basis')
            kind = mapping['period_kind']
            if mapping['period_label'] != rows[0][c]:
                raise ValueError('Period label must match the original column label')
            if kind not in {'fiscal_quarter', 'calendar_quarter', 'annual', 'source_quarter_unmapped'}:
                raise ValueError('Explicit period kind required')
            period = mapping.get('fiscal_period')
            if kind == 'fiscal_quarter':
                if not period or len(period) != 2 or type(period[0]) is not int or not 1 <= period[0] <= 4 or type(period[1]) is not int or not 2000 <= period[1] <= 2100:
                    raise ValueError('Explicit fiscal quarter and year required')
                literal = f'Q{period[0]} FY{period[1]}'
                if literal.lower() not in rows[0][c].lower():
                    raise ValueError('Fiscal mapping lacks a literal original FY label')
            elif period is not None:
                raise ValueError('Unmapped or calendar periods cannot carry a fiscal quarter')
            value = number(rows[r][c])
            if statistic == 'analyst_count' and (value < 1 or not value.is_integer() or unit != 'count'):
                raise ValueError('Analyst count must be a positive integer count')
            if statistic == 'standard_deviation' and value < 0:
                raise ValueError('Standard deviation cannot be negative')
            metric = mapping['metric']
            if statistic != 'analyst_count' and ((metric == 'diluted_eps' and unit != 'USD_per_share') or
                    (metric == 'revenue' and unit not in {'USD', 'USD_millions', 'USD_billions'})):
                raise ValueError('Unit is incompatible with the financial metric')
            record = {**{k: metadata.get(k) for k in ('url', 'issuer', 'published_at_utc', 'captured_at_utc',
                'upstream_id', 'upstream_identity_evidence', 'publication_time_evidence')},
                **{k: mapping[k] for k in ('metric', 'basis', 'statistic', 'source_unit', 'period_kind', 'period_label')},
                'fiscal_period': period, 'raw_value': rows[r][c], 'value': value * UNITS[unit],
                'normalized_unit': 'USD' if unit.startswith('USD_') and unit != 'USD_per_share' else unit,
                'source_sha256': source_hash, 'grid_sha256': sha(canonical),
                'original_cell': {'row': r, 'column': c, 'text': rows[r][c],
                    'row_label': rows[r][0], 'column_label': rows[0][c]},
                'statistic_header_ref': statistic_ref,
                'mapping_sha256': sha(json.dumps(mapping, sort_keys=True)),
                'semantic_interpretation_verified': False, 'role': 'consensus_estimate'}
            record['record_id'] = sha(json.dumps(record, sort_keys=True))
            if record['record_id'] not in {x['record_id'] for x in records}:
                records.append(record)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            gaps.append({'mapping': mapping, 'reason': str(exc)})
    return {'schema': VERSION, 'metadata': metadata, 'source_sha256': source_hash,
        'source_format': 'html' if raw_source is not None else 'explicit_grid',
        'raw_source': raw_source, 'original_grid': rows, 'grid_sha256': sha(canonical),
        'records': records, 'gaps': gaps, 'provider_calls': 0,
        'analyst_dispersion_is_forecast_uncertainty': False}


def validate(package):
    canonical = json.dumps(package['original_grid'], ensure_ascii=False, separators=(',', ':'))
    if sha(canonical) != package['grid_sha256'] or sha(package['raw_source'] if package['raw_source'] is not None else canonical) != package['source_sha256']:
        raise ValueError('Consensus source or grid changed')
    if package['raw_source'] is not None:
        tables, _ = html_tables(package['raw_source'])
        if not any(t['rows'] == package['original_grid'] for t in tables):
            raise ValueError('Grid does not match preserved HTML')
    for record in package['records']:
        cell = record['original_cell']; rows = package['original_grid']
        if rows[cell['row']][cell['column']] != cell['text'] or record['value'] != number(cell['text']) * UNITS[record['source_unit']]:
            raise ValueError('Consensus cell or converted value changed')
        if record['source_sha256'] != package['source_sha256'] or record['grid_sha256'] != package['grid_sha256']:
            raise ValueError('Consensus record source differs')
        stat = record.get('statistic_header_ref')
        if stat and rows[stat['row']][stat['column']] != stat['text']:
            raise ValueError('Consensus statistic header changed')
        unsigned = {k: v for k, v in record.items() if k != 'record_id'}
        if record['record_id'] != sha(json.dumps(unsigned, sort_keys=True)):
            raise ValueError('Consensus record changed')


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('UTC offset required')
    return result.astimezone(timezone.utc)


def assess(record, target, *, as_of, max_age_days=45):
    """Compatibility is distinct from source verification and freshness."""
    gaps = []
    for key in ('issuer', 'metric', 'basis', 'normalized_unit', 'fiscal_period'):
        if record.get(key) != target.get(key) or record.get(key) is None or (key == 'basis' and record.get(key) == 'unknown'):
            gaps.append('target_' + key + '_mismatch_or_unknown')
    if record.get('period_kind') != 'fiscal_quarter':
        gaps.append('fiscal_period_unestablished')
    cutoff = timestamp(as_of)
    for field in ('captured_at_utc', 'published_at_utc'):
        try:
            date = timestamp(record[field])
            if date > cutoff:
                gaps.append(field + '_after_cutoff')
            if field == 'published_at_utc' and (cutoff - date).total_seconds() > max_age_days * 86400:
                gaps.append('publication_stale')
        except (ValueError, TypeError, KeyError, AttributeError):
            gaps.append(field + '_unknown_or_invalid')
    if not record.get('publication_time_evidence'):
        gaps.append('publication_time_unverified')
    try:
        if timestamp(record['published_at_utc']) > timestamp(record['captured_at_utc']):
            gaps.append('publication_after_capture')
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    time_gaps = [g for g in gaps if g.startswith(('captured_at_', 'published_at_', 'publication_'))]
    return {'record_id': record['record_id'], 'compatible': not any(g.startswith(('target_', 'fiscal_')) for g in gaps),
            'as_of_eligible': not time_gaps, 'temporal_status': 'eligible_declared_time' if not time_gaps else 'needs_review',
            'publication_time_independently_verified': False, 'gaps': gaps}


def lineage(records):
    groups, unknown = {}, []
    for record in records:
        upstream = record.get('upstream_id')
        if upstream and record.get('upstream_identity_evidence'):
            groups.setdefault(upstream, []).append(record['record_id'])
        else:
            unknown.append(record['record_id'])
    return {'declared_upstream_groups': groups, 'unknown_upstream_record_ids': unknown,
            'identified_upstream_count': len(groups), 'independence_verified': False,
            'analyst_count_is_source_count': False}


def revisions(records):
    """Observed changes within one declared series; never compare different bases."""
    groups, gaps = {}, []
    for record in records:
        if not record.get('upstream_id') or not record.get('upstream_identity_evidence') or not record.get('publication_time_evidence'):
            gaps.append({'record_id': record['record_id'], 'reason': 'Revision series identity or publication time unestablished'})
            continue
        try:
            timestamp(record['published_at_utc'])
        except (ValueError, TypeError, AttributeError):
            gaps.append({'record_id': record['record_id'], 'reason': 'Invalid publication time'})
            continue
        key = (record['issuer'], record['upstream_id'], record['metric'], record['basis'],
            record['period_kind'], record['period_label'], record['normalized_unit'], record['statistic'])
        groups.setdefault(key, {})[record['record_id']] = record
    changes = []
    for group in groups.values():
        ordered = sorted(group.values(), key=lambda r: timestamp(r['published_at_utc']))
        for old, new in zip(ordered, ordered[1:]):
            if timestamp(old['published_at_utc']) == timestamp(new['published_at_utc']):
                if old['value'] != new['value']:
                    gaps.append({'record_id': new['record_id'], 'reason': 'Conflicting values at identical publication time'})
                continue
            changes.append({'old_record_id': old['record_id'], 'new_record_id': new['record_id'],
                'old_value': old['value'], 'new_value': new['value'], 'delta': new['value'] - old['value'],
                'normalized_unit': new['normalized_unit'], 'series_identity_independently_verified': False})
    return {'changes': changes, 'gaps': gaps}
