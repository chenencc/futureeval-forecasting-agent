"""Program-owned rule clocks and minimum material witnesses, never outcomes.

Unknown dates remain unknown. These conservative gates do not establish full
series coverage, final votes, or the truth of a reported number.
"""
import calendar
import re

MONTHS={calendar.month_name[i].lower():i for i in range(1,13)}
MONTH_PATTERN='|'.join(MONTHS)


def clock(request):
    rules=request.get('resolution_criteria') or ''
    match=re.search(r'(?:release date\s+(?:occurring|occuring)\s+in|(?:release|publication)\s+(?:in|during))\s+('+MONTH_PATTERN+r')\s+(20\d{2})',rules,re.I)
    if not match:
        return {'kind':'unspecified','origin':'resolution_criteria','observation_month_inferred':False}
    return {'kind':'release_month','year':int(match[2]),'month':MONTHS[match[1].lower()],
        'origin':'resolution_criteria','rule_quote':match[0],'observation_month_inferred':False,
        'selection':'first_release_in_month' if re.search(r'first',request.get('fine_print') or '',re.I) else 'unspecified'}


def attach(request, need):
    return {**need,'question_clock':clock(request)}


def publication_dates(body):
    """Extract only publication-linked date witnesses, including Japanese notices."""
    found=[]
    for m in re.finditer(r'('+MONTH_PATTERN+r')\s+(\d{1,2}),?\s+(20\d{2})',body[:2000],re.I):
        context=body[max(0,m.start()-60):m.end()+80]
        if re.search(r'releas|publish|publication',context,re.I):
            found.append({'year':int(m[3]),'month':MONTHS[m[1].lower()],'day':int(m[2]),'quote':context})
    for m in re.finditer(r'令和(\d+)年(\d+)月(\d+)日公表',body[:2000]):
        found.append({'year':2018+int(m[1]),'month':int(m[2]),'day':int(m[3]),'quote':m[0]})
    return found


def issues(need, binding):
    text=need.get('condition',''); quote=binding.get('quote','')
    result=[]
    target=need.get('question_clock',{})
    if target.get('kind')=='release_month':
        dates=publication_dates(binding.get('document_context','') or quote)
        if not any((d['year'],d['month'])==(target['year'],target['month']) for d in dates):
            result.append('required_release_month_unverified')
    if re.search(r'\b(?:resolutions?.*passed|passed.*resolutions?|votes on.*resolutions?)\b',text,re.I):
        stage=re.search(r'\b(?:passed|adopted|approved|on agreeing to the resolution)\b',quote,re.I)
        event=re.search(r'censur|expel|expuls|reprimand',quote,re.I)
        if not stage or not event or re.search(r'\bon motion to table\b',quote,re.I):
            result.append('specific_event_disposition_unverified')
    if re.search(r'ethics committee reports|committee.*reports and recommendations',text,re.I):
        if not re.search(r'ethics',quote,re.I) or not re.search(r'\breport\b',quote,re.I):
            result.append('specific_committee_report_unverified')
    if re.search(r'contemporary news',text,re.I) and not re.search(r'\b20\d{2}\b',quote):
        result.append('dated_news_record_unverified')
    if re.search(r'market cap|capitalization|total viewers|index value|earnings figure',text,re.I):
        # Exact quoted numbers must be tied to the requested measure, not dates
        # or unrelated mentions somewhere else in a long document.
        metric=re.search(r'(?:market cap(?:italization)?|total viewers|index|ＤＩ|DI|earnings)[^\n]{0,100}\d|\d[^\n]{0,70}(?:market cap|total viewers)',quote,re.I)
        if not metric:
            result.append('quoted_metric_observation_unverified')
    if re.search(r'historical.*(?:market cap|trajectory)',text,re.I):
        dates=re.findall(r'('+MONTH_PATTERN+r')\s+(?:\d{1,2},?\s+)?(20\d{2})',text,re.I)
        for month,year in dates:
            if not re.search(re.escape(month)+r'[^\n]{0,25}'+year+r'|'+year+r'-'+f'{MONTHS[month.lower()]:02}',quote,re.I):
                result.append('historical_target_period_unverified')
                break
    return result
