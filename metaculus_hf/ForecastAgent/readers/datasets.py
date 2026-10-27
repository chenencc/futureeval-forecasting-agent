"""Date-aware navigation of saved rows; no network or interpretation."""
from datetime import date
import json
from ForecastAgent.readers.saved import integer


def observation_range(page):
    dates = [str(r.get('date', r.get('observation_date', '')))[:10]
             for r in page.get('rows', []) if isinstance(r, dict)]
    dates = sorted(d for d in dates if len(d) == 10 and d[4] == '-' and d[7] == '-')
    return {'observed_start':dates[0] if dates else None,
            'observed_end':dates[-1] if dates else None, 'source_row_count':len(page.get('rows', []))}


def read_rows(page, args):
    rows = page['rows']
    start, end = args.get('start_date'), args.get('end_date')
    if bool(start) != bool(end):
        raise ValueError('Provide paired start_date and end_date in YYYY-MM-DD format')
    if start:
        if date.fromisoformat(start).isoformat() != start or date.fromisoformat(end).isoformat() != end or start > end:
            raise ValueError('Use an ordered ISO date range')
    selected = [(index, row) for index, row in enumerate(rows)
                if not start or start <= str(row.get('date', row.get('observation_date', '')))[:10] <= end]
    offset = integer(args.get('offset',0),0,len(selected),'offset')
    limit = integer(args.get('limit',50),1,100,'limit')
    batch = selected[offset:offset+limit]
    locations = []
    documents = page.get('documents', [])
    for index, row in batch:
        location = {'source_row_index':index}
        if index < len(documents):
            try:
                if json.loads(documents[index]['page_content']) == row:
                    location['document_index'] = index + 1
            except (ValueError, KeyError, TypeError):
                pass
        locations.append(location)
    return {'url':args['url'], 'rows':[row for _,row in batch], 'row_locations':locations,
            'total':len(selected), 'offset':offset,
            'next_offset':offset+len(batch) if offset+len(batch)<len(selected) else None,
            'date_filter':{'start_date':start,'end_date':end}, **observation_range(page),
            'warning':page.get('data_warning'), 'unit':page.get('unit'),
            'instruction':'total and next_offset refer to the filtered view. A first page is not the complete dataset. Use exact row document handles to preserve excerpts.'}
