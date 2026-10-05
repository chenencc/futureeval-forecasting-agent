"""Literal source values and record-local arithmetic with exact provenance.

Parsing supports explicit USD, percent, MW/GW and complete English/ISO dates.
Unsupported units, omitted years and ambiguous prose remain unparsed. A local
comparison never refutes existence of a qualifying record in another instant.
"""
import re
from datetime import date, datetime
from decimal import Decimal
from ForecastAgent.supplement import acquisition_contract as base

NUMBER = r'\d+(?:,\d{3})*(?:\.\d+)?'
QUANTITY = re.compile(r'(?:\$\s*' + NUMBER + r'(?:\s*(?:billion|million|thousand))?|'
                      + NUMBER + r'\s*(?:(?:USD|MW|GW)\b|%))', re.I)
DATES = re.compile(r'\b(?:\d{4}-\d{2}-\d{2}|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})\b', re.I)


def quantity(text):
    clean = text.strip().replace(',', '')
    if not QUANTITY.fullmatch(clean):
        return None
    value = Decimal(re.search(NUMBER, clean).group())
    unit = 'USD' if '$' in clean or 'USD' in clean.upper() else re.search(r'(MW|GW|%)$', clean, re.I).group().upper()
    scale = {'billion': 10**9, 'million': 10**6, 'thousand': 10**3}
    for word, multiplier in scale.items():
        if word in clean.lower():
            value *= multiplier
    if unit == 'GW':
        value *= 1000
        unit = 'MW'
    return {'value': str(value), 'unit': unit}


def parse_date(text):
    clean = re.sub(r'(\d)(st|nd|rd|th)\b', r'\1', text, flags=re.I).replace(',', '')
    try:
        return date.fromisoformat(clean).isoformat()
    except ValueError:
        try:
            return datetime.strptime(clean, '%B %d %Y').date().isoformat()
        except ValueError:
            return None


def inventory(binding):
    if not binding or binding['kind'] != 'source':
        return []
    records = []
    for kind, pattern, parse in [('quantity', QUANTITY, quantity), ('date', DATES, parse_date)]:
        for match in pattern.finditer(binding['text']):
            normalized = parse(match.group())
            if normalized is None:
                continue
            item = {'kind': kind, 'text': match.group(), 'normalized': normalized,
                    'url': binding['url'], 'body_sha256': binding['body_sha256'],
                    'start': binding['start'] + match.start(), 'end': binding['start'] + match.end()}
            item['value_id'] = 'VAL-' + base.sha(str((item['url'], item['start'], item['end'], kind)))[:18]
            records.append(item)
    return records[:128]


def verify(item, bundle):
    body = bundle['pages'][item['url']]['content']
    if base.sha(body) != item['body_sha256'] or body[item['start']:item['end']] != item['text']:
        raise ValueError('source_value_changed')


def compare(item, target):
    """Compare literals only; neither field relevance nor event truth is certified."""
    if not item:
        return None
    if item['kind'] == 'quantity':
        expected = quantity(target)
        if expected is None or expected['unit'] != item['normalized']['unit']:
            return None
        actual, threshold = Decimal(item['normalized']['value']), Decimal(expected['value'])
    else:
        expected = parse_date(target)
        if expected is None:
            return None
        actual, threshold = item['normalized'], expected
    return {'ordering': 'less' if actual < threshold else 'greater' if actual > threshold else 'equal',
            'actual': item['normalized'], 'target': expected, 'record_local_only': True,
            'field_relevance_verified': False, 'world_event_refutation': False}
