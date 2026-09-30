"""Source leads and explicit date-bounded data adapters; no search or model calls."""
import base64
import csv
from datetime import datetime, timedelta, timezone
import io
import json
import re
from urllib.parse import urlsplit, unquote, quote, urlencode

from ForecastAgent.tavily_research import canonical_url
from ForecastAgent.evidence.document import Document

MONTHS = {name: i for i, name in enumerate(['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], 1)}


def quoted_dates(text):
    dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text)
    for month, day, year in re.findall(r'\b([A-Z][a-z]{2})\s+(\d{1,2}),?\s+(\d{4})\b', text):
        if month in MONTHS:
            dates.append(f'{year}-{MONTHS[month]:02d}-{int(day):02d}')
    return dates


def source_urls(text):
    return list(dict.fromkeys(u.rstrip('.,;') for u in re.findall(r'https?://[^\s<>"\[\]()]+', text) if canonical_url(u)))


def allowed_source(url):
    parts = urlsplit(url)
    host = (parts.hostname or '').lower().rstrip('.')
    return bool(canonical_url(url)) and not parts.username and not parts.password and not (host == 'metaculus.com' or host.endswith('.metaculus.com'))


def yahoo_symbol(url):
    parts = urlsplit(url)
    match = re.fullmatch(r'/quote/([^/]+)(?:/history)?/?', parts.path)
    symbol = unquote(match.group(1)) if parts.hostname == 'finance.yahoo.com' and match else None
    return symbol if symbol and re.fullmatch(r'[A-Za-z0-9^=.-]{1,25}', symbol) else None


def structured_url(url):
    parts = urlsplit(url)
    return bool(yahoo_symbol(url) or (parts.hostname in {'fred.stlouisfed.org', 'alfred.stlouisfed.org'} and re.fullmatch(r'/series/[A-Za-z0-9_]+', parts.path)))


def fetch_structured(url, cutoff, fetch):
    """Yahoo daily history / ALFRED vintage; provider calls count as free fetches."""
    symbol = yahoo_symbol(url)
    parts = urlsplit(url)
    fred = re.fullmatch(r'/series/([A-Za-z0-9_]+)', parts.path) if parts.hostname in {'fred.stlouisfed.org', 'alfred.stlouisfed.org'} else None
    if not symbol and not fred:
        return None
    end = cutoff or datetime.now(timezone.utc)
    if symbol:
        query = urlencode({'period1': int((end - timedelta(days=390)).timestamp()), 'period2': int(end.timestamp()), 'interval': '1d'})
        api_url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + quote(symbol, safe='') + '?' + query
        page = fetch(api_url)
        data = json.loads(base64.b64decode(page['raw_response_base64']))
        result = data['chart']['result'][0]
        closes = result['indicators']['quote'][0]['close']
        rows = []
        withheld = 0
        for stamp, close in zip(result.get('timestamp', []), closes):
            if close is None:
                continue
            observed = datetime.fromtimestamp(stamp, timezone.utc)
            # Conservative full UTC day cutoff. Avoid treating the morning bar timestamp
            # as publication of that day's closing value (even for current partial bars).
            available = observed.replace(hour=23, minute=59, second=59, microsecond=0)
            if available >= end:
                withheld += 1
                continue
            rows.append({'date': observed.date().isoformat(), 'close': close, 'conservative_available_at': available.isoformat()})
        title = f'Yahoo Finance daily history: {symbol}; cutoff {end.isoformat()}'
        warning = 'Daily closes conservatively exclude the cutoff UTC day. Current-vintage/adjustment revisions remain possible; not a verified historical capture.'
    else:
        ident = fred.group(1)
        # A vintage BEFORE the cutoff calendar day avoids unknown same-day release times.
        vintage = (end.date() - timedelta(days=1)).isoformat()
        # Keyless ALFRED download carries the selected vintage, unlike current FRED CSV.
        api_url = 'https://alfred.stlouisfed.org/graph/alfredgraph.csv?' + urlencode({'id': ident, 'vintage_date': vintage, 'cosd': (end-timedelta(days=390)).date().isoformat(), 'coed': vintage})
        page = fetch(api_url)
        reader = csv.DictReader(io.StringIO(base64.b64decode(page['raw_response_base64']).decode('utf-8-sig')))
        columns = reader.fieldnames or []
        vintage_columns = {ident + '_' + vintage, ident + '_' + vintage.replace('-', '')}
        value_columns = [c for c in columns if c in vintage_columns]
        if not value_columns:
            raise ValueError('ALFRED response did not confirm requested vintage; refusing current series')
        rows = []; withheld = 0
        for row in reader:
            day = row.get('observation_date') or row.get('DATE') or row.get('date')
            value = row.get(value_columns[0])
            if not day or day > vintage or value in {None, '', '.'}:
                withheld += 1; continue
            rows.append({'date': day, 'value': float(value), 'vintage': vintage})
        title = f'ALFRED series {ident}; vintage {vintage}'
        warning = 'Vintage excludes cutoff day; verify series identity and release cadence. ALFRED vintage is not proof against model knowledge leakage.'
    if not rows:
        raise ValueError('No eligible observations before cutoff')
    page.update(url=url, data_endpoint=api_url, capture_method='structured_data', rows=rows,
                content=title+'\n'+warning+'\n'+ '\n'.join(json.dumps(r, sort_keys=True) for r in rows),
                content_truncated=False, withheld_rows=withheld, data_warning=warning, links=[])
    page["documents"] = [Document(json.dumps(row, sort_keys=True), {"source": url, "data_endpoint": api_url, "format": "series", "row": i+1, "cutoff": end.isoformat(), "retrieved_at_utc": page.get("retrieved_at_utc")}).as_dict() for i, row in enumerate(rows)]
    page["document_count"] = len(rows)
    page["documents_truncated"] = False
    return page
