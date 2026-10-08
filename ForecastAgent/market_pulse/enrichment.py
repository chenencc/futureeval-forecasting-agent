"""Immutable, bounded official financial supplements, without paid search.

Observed SEC archive identifiers establish CIK provenance. Company facts keep
their accession, filing vintage and exact duration; fiscal labels are not
inferred from a calendar quarter or the reporting filing's FY/FP fields.
"""
import argparse
import copy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.request import build_opener

from ForecastAgent.analysis.pilot import digest, load, save
from ForecastAgent.providers.http import download
from ForecastAgent.providers.ultra import public_url, SafeRedirects
from ForecastAgent.readers.loader import load_response
from ForecastAgent.runtime.task_lock import task_lock

VERSION = 'market-pulse-official-supplement-v1'
CONCEPTS = {
    'revenue': {'RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet'},
    'diluted_eps': {'EarningsPerShareDiluted'},
    'net_income': {'NetIncomeLoss'},
    'diluted_shares': {'WeightedAverageNumberOfDilutedSharesOutstanding'},
    'operating_income': {'OperatingIncomeLoss'},
    'tax_expense': {'IncomeTaxExpenseBenefit'},
    'pretax_income': {'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
                      'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments'},
}
UNITS = {'revenue': 'USD', 'diluted_eps': 'USD/shares', 'net_income': 'USD',
         'diluted_shares': 'shares', 'operating_income': 'USD', 'tax_expense': 'USD', 'pretax_income': 'USD'}


def observed_cik(bundle):
    found = {}
    for url in bundle.get('pages', {}):
        m = re.search(r'https://www\.sec\.gov/Archives/edgar/data/(\d+)/', url)
        if m:
            found.setdefault(int(m[1]), []).append(url)
    if len(found) != 1:
        raise ValueError('Exactly one observed SEC archive CIK is required; no title-based guess')
    cik, urls = next(iter(found.items()))
    return {'cik': cik, 'provenance_urls': urls, 'identity_verification': 'observed_archive_identifier; companyfacts name must be reviewed'}


def quarter_history(data, cutoff, *, earliest='2018-01-01'):
    """First available SEC filing vintage, not guaranteed first earnings release.

    Annual/YTD rows and unfiled future data are excluded. Q4 is not fabricated by
    subtracting EPS or share averages. Exact start/end plus unit/concept define
    a record; multiple concept aliases remain separate pending reconciliation.
    """
    result = []
    for metric, concepts in CONCEPTS.items():
        for concept in sorted(concepts):
            tag = data.get('facts', {}).get('us-gaap', {}).get(concept, {})
            for unit, rows in tag.get('units', {}).items():
                if unit != UNITS[metric]:
                    continue
                grouped = {}
                for row in rows:
                    try:
                        duration = (date.fromisoformat(row['end']) - date.fromisoformat(row['start'])).days + 1
                        filed = date.fromisoformat(row['filed'])
                    except (ValueError, KeyError, TypeError):
                        continue
                    if not 70 <= duration <= 105 or not earliest <= row['end'] < cutoff or filed >= date.fromisoformat(cutoff) or filed < date.fromisoformat(row['end']):
                        continue
                    if row.get('form') not in {'10-Q', '10-K', '8-K'}:
                        continue
                    grouped.setdefault((row['start'], row['end']), []).append(row)
                for (start, end), versions in sorted(grouped.items()):
                    first = sorted(versions, key=lambda r: (r['filed'], r.get('accn', ''), str(r.get('val'))))[0]
                    first_values = {v['val'] for v in versions if v['filed'] == first['filed'] and v.get('accn') == first.get('accn')}
                    result.append({'metric': metric, 'concept': concept, 'unit': unit,
                        'start': start, 'end': end, 'duration_days': (date.fromisoformat(end)-date.fromisoformat(start)).days+1,
                        'value': first['val'], 'filed': first['filed'], 'accession': first.get('accn'),
                        'form': first['form'], 'filing_fy': first.get('fy'), 'filing_fp': first.get('fp'),
                        'frame_hint': first.get('frame'), 'vintages': len(versions),
                        'same_first_accession_conflict': len(first_values) != 1,
                        'first_report_verified': False,
                        'warning': 'First available SEC filing vintage; publication may follow the first earnings release. Filing FY/FP is not the observation fiscal quarter.'})
    return result


def capture(url, folder, kind, cutoff):
    folder.mkdir(parents=True, exist_ok=True)
    if (folder/'receipt.json').exists():
        return load(folder/'receipt.json')
    # The reservation is durable before HTTP; failed/interrupted reservations do not retry silently.
    receipt = {'url': url, 'kind': kind, 'started_at_utc': datetime.now(timezone.utc).isoformat(),
               'status': 'reserved', 'physical_http_attempts': 1, 'paid_search_calls': 0}
    save(folder/'receipt.json', receipt)
    try:
        response = download(url, public_check=public_url,
            opener_factory=lambda: build_opener(SafeRedirects()), max_page_bytes=16_000_000,
            user_agent='ForecastAgentResearch/1.0 (https://github.com/chenencc/futureeval-forecasting-agent)')
        (folder/'response.bin').write_bytes(response['raw'])
        receipt.update(status='received', raw_sha256=hashlib.sha256(response['raw']).hexdigest(),
            raw_bytes=len(response['raw']), final_url=response['final_url'],
            content_type=response['content_type'], response_headers=response.get('response_headers', {}))
        if kind == 'companyfacts':
            data = json.loads(response['raw'])
            expected = int(re.search(r'CIK(\d+)\.json', url)[1])
            if int(data.get('cik', -1)) != expected:
                raise ValueError('Company facts CIK mismatches observed archive identifier')
            history = quarter_history(data, cutoff)
            save(folder/'history.json', {'entity_name': data.get('entityName'), 'cik': expected,
                'raw_sha256': receipt['raw_sha256'], 'rows': history})
            receipt['quarterly_rows'] = len(history)
        else:
            page = load_response(response, retrieved_at=receipt['started_at_utc'], max_chars=600_000,
                                 preserve_raw_on_failure=True)
            save(folder/'page.json', page)
            receipt['readable_chars'] = len(page.get('content', ''))
            receipt['parsed_documents'] = len(page.get('documents', []))
            if not page.get('body_diagnostics', {}).get('usable_text', bool(page.get('content'))):
                receipt['status'] = 'unusable_body'
    except Exception as exc:
        receipt.update(status='failed', error_type=type(exc).__name__, error=str(exc)[:300])
        if isinstance(exc,HTTPError):
            receipt['http_status']=exc.code
            raw=exc.read(32_001)
            (folder/'failed-response.bin').write_bytes(raw)
            receipt['failed_response_sha256']=hashlib.sha256(raw).hexdigest()
            receipt['failed_response_truncated']=len(raw)>32_000
    receipt['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    save(folder/'receipt.json', receipt)
    return receipt


def run(root, baseline, plan):
    sources = load(baseline/'manifest.json')['inputs']
    root.mkdir(parents=True, exist_ok=True)
    manifest = {'protocol': VERSION, 'original_inputs': sources, 'plan': plan,
        'as_of_date': datetime.now(timezone.utc).date().isoformat(),
        'free_http_max': len(sources)+sum(sum(s['id'] in item['ids'] for item in plan) for s in sources),
        'tavily_calls': 0, 'exa_calls': 0, 'model_calls': 0, 'submitted': False}
    with task_lock(root):
        if (root/'manifest.json').exists() and load(root/'manifest.json') != manifest:
            raise ValueError('Immutable supplementation identity changed')
        save(root/'manifest.json', manifest)
        receipts=[]
        for source in sources:
            original_path=Path(source['source']); original=load(original_path)
            if hashlib.sha256(original_path.read_bytes()).hexdigest()!=source['sha256']:
                raise ValueError('Original acquisition snapshot changed')
            folder=root/'tasks'/source['id']; folder.mkdir(parents=True, exist_ok=True)
            identity=observed_cik(original); save(folder/'observed-issuer.json', identity)
            urls=[{'url':f"https://data.sec.gov/api/xbrl/companyfacts/CIK{identity['cik']:010d}.json", 'kind':'companyfacts'}]
            urls += [p for p in plan if source['id'] in p['ids']]
            merged=copy.deepcopy(original); history=[]
            for i, item in enumerate(urls):
                record=capture(item['url'], folder/'captures'/str(i+1), item['kind'], manifest['as_of_date'])
                receipts.append({'id':source['id'],**record})
                if record['status']=='received':
                    if item['kind']=='companyfacts': history=load(folder/'captures'/str(i+1)/'history.json')['rows']
                    else: merged['pages'][item['url']]=load(folder/'captures'/str(i+1)/'page.json')
                time.sleep(.25)
            save(folder/'quarterly-history.json',history)
            merged['official_supplement_lineage']={'root':str(folder), 'original_sha256':source['sha256'],
                'captured_at_utc':datetime.now(timezone.utc).isoformat(), 'receipts':receipts[-len(urls):],
                'historical_first_release_not_guaranteed':True}
            save(folder/'enriched-package.json', merged)
            print(json.dumps({'id':source['id'],'free_http':len(urls),'quarterly_rows':len(history),
                'successful':sum(r['status']=='received' for r in receipts[-len(urls):])}),flush=True)
        summary={'protocol':VERSION, 'receipts':receipts, 'free_http_attempts':len(receipts),
            'original_snapshots_unchanged':all(hashlib.sha256(Path(s['source']).read_bytes()).hexdigest()==s['sha256'] for s in sources),
            'tavily_calls':0,'exa_calls':0,'model_calls':0,'submitted':False}
        save(root/'summary.json',summary)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--plan',type=Path,required=True)
    args=parser.parse_args();run(args.root,args.baseline,load(args.plan))
