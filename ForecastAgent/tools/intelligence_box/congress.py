"""Preserve official bill versions and stages without inferring legal effects."""
import re
from urllib.parse import urlsplit

BILL_TYPES = {'hr', 's', 'hjres', 'sjres', 'hconres', 'sconres', 'hres', 'sres'}


def validate_parameters(params):
    for key in ('congress', 'bill_number'):
        if not re.fullmatch(r'[1-9][0-9]{0,5}', str(params.get(key, ''))):
            raise ValueError('Exact positive Congress and bill numbers required')
    if params.get('bill_type') not in BILL_TYPES:
        raise ValueError('Use an exact lowercase bill type from the catalog')
    for key, low, high in (('limit', 1, 250), ('offset', 0, 100000)):
        if key in params and (isinstance(params[key], bool) or not re.fullmatch(r'[0-9]+', str(params[key])) or not low <= int(params[key]) <= high):
            raise ValueError('Invalid Congress page limit or offset')


def parse_envelope(kind, body):
    if not isinstance(body, dict) or 'error' in body:
        raise ValueError('Invalid Congress API envelope')
    field = {'congress_bill': 'bill', 'congress_actions': 'actions', 'congress_texts': 'textVersions'}[kind]
    records = [body[field]] if kind == 'congress_bill' else body[field]
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError('Invalid Congress records')
    if kind == 'congress_bill' and not records[0]:
        raise ValueError('Empty Congress bill detail')
    pagination = body.get('pagination', {})
    if not isinstance(pagination, dict):
        raise ValueError('Invalid Congress pagination')
    total = pagination.get('count', len(records))
    if not isinstance(total, int) or total < len(records):
        raise ValueError('Invalid Congress record count')
    metadata = {k: v for k, v in body.items() if k != field}
    if kind == 'congress_bill':
        metadata['bill_identity'] = {k: records[0].get(k) for k in ('congress', 'type', 'number')}
    return records, total, bool(pagination.get('next')) or total > len(records), metadata


def bind_identity(source_id, params, metadata):
    identity = metadata.get('bill_identity') if source_id == 'congress_bill' else metadata.get('request')
    if not isinstance(identity, dict):
        raise ValueError('Congress response is missing bill identity')
    keys = ('congress', 'type', 'number') if source_id == 'congress_bill' else ('congress', 'billType', 'billNumber')
    expected = (str(params['congress']), params['bill_type'], str(params['bill_number']))
    actual = tuple(str(identity.get(k, '')).lower() for k in keys)
    if actual != expected:
        raise ValueError('Returned Congress bill identity does not match request')
    return {'congress': int(params['congress']), 'bill_type': params['bill_type'],
            'bill_number': int(params['bill_number']), 'basis': 'Exact official bill identity',
            'enactment_inferred': False, 'commencement_inferred': False}


def select_text(capture, version_index, format_index, detail=None):
    if capture.get('source_id') != 'congress_texts' or capture.get('status') != 'usable':
        raise ValueError('A usable saved Congress text index is required')
    if any(type(i) is not int or i < 0 for i in (version_index, format_index)):
        raise ValueError('Select explicit nonnegative version and format indices')
    try:
        version = capture['records'][version_index]
        fmt = version['formats'][format_index]
    except (IndexError, KeyError, TypeError):
        raise ValueError('Selected Congress text version or format does not exist') from None
    url = fmt.get('url', '')
    parts = urlsplit(url)
    identity = capture['source_binding']
    pattern = r'BILLS-' + str(identity['congress']) + identity['bill_type'] + str(identity['bill_number']) + r'(?=[a-z]|\.)'
    law_binding=None
    matched=re.search(pattern,parts.path)
    if not matched and detail:
        if detail.get('source_id')!='congress_bill' or detail.get('status')!='usable':
            raise ValueError('Public law text requires a usable saved bill detail')
        if any(detail.get('source_binding',{}).get(k)!=identity[k] for k in ('congress','bill_type','bill_number')):
            raise ValueError('Bill detail and text index must have the same identity')
        for law in detail['records'][0].get('laws',[]):
            prefix={'Public Law':'publ','Private Law':'priv'}.get(law.get('type'))
            number=str(law.get('number',''))
            if prefix and re.fullmatch(r'[1-9][0-9]*-[1-9][0-9]*',number):
                congress,sequence=number.split('-')
                if congress==str(identity['congress']) and re.search(r'PLAW-'+congress+prefix+sequence+r'(?=[_.]|$)',parts.path):
                    matched=True
                    law_binding={'detail_capture_id':detail['id'],'detail_raw_sha256':detail['raw_sha256'],'official_law_record':law}
                    break
    if parts.hostname not in {'www.congress.gov', 'www.govinfo.gov'} or not matched:
        raise ValueError('Original text URL must match the exact bill on an official text host')
    return url, {'index_capture_id': capture['id'], 'index_raw_sha256': capture['raw_sha256'],
                 'bill_identity': identity, 'version_index': version_index, 'format_index': format_index,
                 'version_type': version.get('type'), 'version_date': version.get('date'),
                 'formats': version.get('formats'), 'selected_format': fmt,
                 'publication_date': version.get('publicationDate'),
                 'law_binding':law_binding,
                 'enactment_inferred': False, 'commencement_inferred': False}
