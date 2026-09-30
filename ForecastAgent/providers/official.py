"""Bounded, keyless government data adapters with explicit provenance."""
import base64
import json
from urllib.parse import urlencode
from datetime import date

DATASETS = {
    'bls_cpi': {'publisher': 'BLS', 'series_id': 'CUUR0000SA0', 'label': 'CPI-U, all items, US city average, not seasonally adjusted',
                'unit': 'Index, 1982-84=100', 'docs': 'https://www.bls.gov/developers/api_signature_v2.htm'},
    'bls_unemployment': {'publisher': 'BLS', 'series_id': 'LNS14000000', 'label': 'Civilian unemployment rate, seasonally adjusted',
                         'unit': 'Percent', 'docs': 'https://www.bls.gov/developers/api_signature_v2.htm'},
    'bls_payrolls': {'publisher': 'BLS', 'series_id': 'CES0000000001', 'label': 'Total nonfarm payroll employment, seasonally adjusted',
                    'unit': 'Thousands of persons', 'docs': 'https://www.bls.gov/developers/api_signature_v2.htm'},
    'treasury_debt': {'publisher': 'US Treasury Fiscal Data', 'label': 'Debt to the Penny', 'unit': 'USD',
                      'docs': 'https://fiscaldata.treasury.gov/datasets/debt-to-the-penny/debt-to-the-penny'},
    'federal_register': {'publisher': 'Office of the Federal Register', 'label': 'Federal Register document discovery', 'unit': None,
                         'docs': 'https://www.federalregister.gov/developers/documentation/api/v1'},
}


def dataset_catalog():
    return {'datasets': [{'id': key, **value, 'temporal_support': 'Current capture only; publication/observation dates are not archived versions.'}
                         for key, value in DATASETS.items()]}


def date_bounds(start_date=None,end_date=None):
    if start_date is None and end_date is None: return None
    if not start_date or not end_date: raise ValueError('Provide both start_date and end_date')
    start,end=date.fromisoformat(start_date),date.fromisoformat(end_date)
    if start>end: raise ValueError('start_date must precede end_date')
    return start,end


def endpoint(dataset, query='', page=1, *, start_date=None, end_date=None):
    if dataset not in DATASETS:
        raise ValueError('Unknown official dataset')
    if type(page) is not int or not 1 <= page <= 3:
        raise ValueError('Official page must be between one and three')
    bounds=date_bounds(start_date,end_date)
    if dataset.startswith('bls_'):
        if page != 1:
            raise ValueError('BLS single-series requests have no pagination')
        suffix=''
        if bounds:
            if bounds[1].year-bounds[0].year>2: raise ValueError('Keyless BLS adapter limits each explicit request to three calendar years')
            suffix='?'+urlencode({'startyear':bounds[0].year,'endyear':bounds[1].year})
        return 'https://api.bls.gov/publicAPI/v2/timeseries/data/' + DATASETS[dataset]['series_id']+suffix
    if dataset == 'treasury_debt':
        params={'sort': '-record_date', 'page[size]': 100, 'page[number]': page, 'format': 'json'}
        if bounds: params['filter']=f'record_date:gte:{start_date},record_date:lte:{end_date}'
        return 'https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_to_penny?' + urlencode(params)
    if not isinstance(query, str) or not query.strip() or len(query) > 250:
        raise ValueError('Federal Register needs a nonempty query of at most 250 characters')
    params={'conditions[term]': query, 'order': 'newest', 'per_page': 20, 'page': page}
    if bounds: params.update({'conditions[publication_date][gte]':start_date,'conditions[publication_date][lte]':end_date})
    return 'https://www.federalregister.gov/api/v1/documents.json?' + urlencode(params)


def fetch_official(dataset, query, page_number, fetch, *, start_date=None, end_date=None):
    url = endpoint(dataset, query, page_number,start_date=start_date,end_date=end_date)
    page = fetch(url)
    data = json.loads(base64.b64decode(page['raw_response_base64']))
    if not isinstance(data, dict):
        raise ValueError('Official response must be an object')
    metadata = DATASETS[dataset]
    links = []
    if dataset.startswith('bls_'):
        if data.get('status') != 'REQUEST_SUCCEEDED':
            raise ValueError('BLS returned an unsuccessful request')
        results = data.get('Results', {})
        if isinstance(results, list):
            results = results[0] if results else {}
        series = results.get('series', [])
        if len(series) != 1 or series[0].get('seriesID') != metadata['series_id']:
            raise ValueError('BLS series identity differs from request')
        rows = series[0].get('data', [])
        pagination = {'has_more': False, 'scope': 'Provider default recent single-series window'}
    elif dataset == 'treasury_debt':
        rows = data.get('data')
        if not isinstance(rows, list):
            raise ValueError('Fiscal Data response lacks rows')
        total_pages = data.get('meta', {}).get('total-pages')
        pagination = {'has_more': int(total_pages) > page_number if total_pages is not None else None,
                      'total_pages': total_pages}
    else:
        rows = data.get('results')
        if not isinstance(rows, list):
            raise ValueError('Federal Register response lacks results')
        links = [row[k] for row in rows for k in ('html_url', 'pdf_url') if isinstance(row.get(k), str)]
        pagination = {'has_more': bool(data.get('next_page_url')), 'total_results': data.get('count')}
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Invalid official rows')
    withheld=0
    if date_bounds(start_date,end_date):
        kept=[]
        for row in rows:
            day=row.get('record_date') or row.get('publication_date')
            if dataset.startswith('bls_'):
                period=row.get('period','')
                if not period.startswith('M') or period=='M13': withheld+=1;continue
                day=str(row.get('year'))+'-'+period[1:]+'-01'
                if not start_date[:7]<=day[:7]<=end_date[:7]: withheld+=1;continue
                kept.append(row);continue
            if not isinstance(day,str) or not start_date<=day[:10]<=end_date: withheld+=1;continue
            kept.append(row)
        rows=kept
    warning = 'Current provider version; observation/publication date does not prove availability at a past cutoff.'
    if dataset == 'federal_register':
        warning += ' FederalRegister.gov is an informational rendition, not the official legal edition; preserve the linked official PDF.'
    content = json.dumps({'dataset': dataset, **metadata, 'warning': warning}, ensure_ascii=False) + '\n'
    content += '\n'.join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    request={'dataset':dataset,'query':query,'page':page_number}
    if start_date: request.update(start_date=start_date,end_date=end_date)
    page.update(url=url, capture_method='official_data', official_request=request,
                requested_range={'start_date':start_date,'end_date':end_date},withheld_rows=withheld,
                dataset=dataset, publisher=metadata['publisher'], unit=metadata['unit'], unit_provenance='adapter_catalog',
                rows=rows, provider_metadata=data.get('meta', {}), provider_messages=data.get('message', []),
                pagination=pagination, data_warning=warning, content=content[:150_000], content_truncated=len(content) > 150_000,
                links=links, documents=[], document_count=len(rows), documents_truncated=False)
    remaining = 150_000
    for index, row in enumerate(rows, 1):
        if remaining <= 0:
            break
        text = json.dumps(row, ensure_ascii=False, sort_keys=True)
        part = text[:remaining]; remaining -= len(part)
        page['documents'].append({'page_content': part, 'metadata': {'source': url, 'format': 'official_row',
             'row': index, 'dataset': dataset, 'unit': metadata['unit'], 'truncated': len(part) < len(text)}})
    page['documents_truncated'] = len(page['documents']) < len(rows) or any(d['metadata']['truncated'] for d in page['documents'])
    return page
