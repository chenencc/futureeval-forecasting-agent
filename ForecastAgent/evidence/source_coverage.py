"""Observable coverage axes for prose and measurements; never resolve events."""
import calendar
import re
from datetime import date
from urllib.parse import unquote

MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
UNITS = {'cm': r'centimet(?:er|re)s?|\bcm\b', 'mm': r'millimet(?:er|re)s?|\bmm\b',
         'percent': r'percent(?:age)?|%', 'usd': r'USD|U\.S\. dollars|\$'}


def calendar_dates(text):
    result = set()
    matches = [(y,m,d) for y,m,d in re.findall(r'\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b', text)]
    matches += [(y,m,d) for d,m,y in re.findall(r'\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b', text)]
    matches += [(y,MONTHS[m.lower()],d) for m,d,y in re.findall(
        r'\b('+ '|'.join(MONTHS) +r')\s+(\d{1,2}),?\s+(20\d{2})\b', text, re.I)]
    for y,m,d in matches:
        try: result.add(date(int(y),int(m),int(d)).isoformat())
        except ValueError: pass
    return sorted(result)


def observe(question, url, body, page=None):
    page = page or {}
    rules = ' '.join(str(question.get(k, '')) for k in ('question','resolution_criteria'))
    # Identifier matching has boundaries: 25700100 is not 125700100.
    identifiers = sorted(set(re.findall(r'\b\d{5,12}\b', rules)))
    content = unquote(url)+' '+body
    matched = [value for value in identifiers if re.search(r'(?<!\d)'+value+r'(?!\d)', body)]
    wanted_dates = calendar_dates(str(question.get('question','')))
    seen_dates = calendar_dates(body)
    url_dates = calendar_dates(unquote(url))
    wanted_units = [key for key,pattern in UNITS.items() if re.search(pattern,rules,re.I)]
    seen_units = [key for key,pattern in UNITS.items() if re.search(pattern,body,re.I)]
    wanted_times = sorted(set(re.findall(r'\b\d{2}:\d{2}\b', rules)))
    seen_times = sorted(set(re.findall(r'\b\d{2}:\d{2}\b', body)))
    measurement_lines = len(re.findall(r'^\s*\d{1,2}:\d{2}\s*[#;,\t ]\s*[-+]?\d+(?:[.,]\d+)?\s*$',body,re.M))
    rows = len(page.get('rows', []))
    date_match = bool(wanted_dates) and set(wanted_dates) <= set(seen_dates)
    id_match = bool(identifiers) and set(identifiers) <= set(matched)
    measurements = rows > 0 or measurement_lines >= 2
    data_candidate = measurements and id_match and date_match and (
        not wanted_units or bool(set(wanted_units) & set(seen_units)))
    return {'identity': {'expected_ids': identifiers, 'observed_ids': matched},
        'dates': {'expected_days':wanted_dates,'observed_days':seen_dates,
                  'url_days':url_dates,'target_day_observed':date_match},
        'units': {'expected':wanted_units,'observed':seen_units},
        'clock': {'expected':wanted_times,'observed':seen_times,
                  'requested_clock_observed': bool(wanted_times) and set(wanted_times)<=set(seen_times),
                  'timezone_verified':False},
        'format': {'saved_rows':rows,'measurement_line_count':measurement_lines},
        'data_capture_candidate':data_candidate,
        'event_timing_verified':False,'metric_verified':False,'truth_verified':False,
        'scope':'Literal identifiers, calendar dates and raw measurements; no value selection or timezone conversion.'}
