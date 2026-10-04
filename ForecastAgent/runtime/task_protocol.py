"""A single effective task clock for every model-facing acquisition request."""
from ForecastAgent.runtime.temporal_policy import unrestricted
from datetime import datetime
import calendar
import re
import json


def task_view(task):
    request = task.bundle['request']
    current = unrestricted(task.bundle) or task.bundle['mode'] == 'live'
    clock = task.bundle.get('control', {}).get('operating_clock_utc')
    from ForecastAgent.supplement.requirement_contract import clock as resolution_clock
    return {
        'question': {k: request[k] for k in ('id', 'question', 'title', 'resolution_criteria',
            'fine_print', 'background') if k in request},
        'operating_mode': 'current_information' if current else task.bundle['mode'],
        'operating_clock_utc': clock if current else request.get('as_of_utc'),
        'effective_cutoff_utc': task.cutoff.isoformat() if task.cutoff else None,
        'resolution_clock':resolution_clock(request),
        'provenance': {'original_as_of_utc': request.get('as_of_utc'),
            'original_input_mode': request.get('mode'), 'request_hash': task.bundle['request_hash']},
        'date_instruction': ('Use operating_clock_utc as now. Original dates are provenance only. '
            'For completed event windows collect realized source records, not forecasts for the remainder of a past month.'
            if current else 'Use operating_clock_utc as the simulated present and respect the effective cutoff.')}


def supplemental_messages(task):
    return [{'role': 'system', 'content':
        'You are the information acquisition agent selecting one supplemental discovery query. '
        'Use the operating clock and effective cutoff in task_protocol. Provenance dates never override them. '
        'Call search_exa for an unresolved acquisition need using its exact need ID. '
        'Prefer category=general for scientific and government domains. Category publication is a specialized '
        'publisher filter, not a synonym for any published document. Use only advertised valid parameter combinations. '
        'Describe the actual event or document. Do not forecast, infer an outcome, or invent URLs.'},
        {'role': 'user', 'content': json.dumps({
            'task_protocol': task_view(task), 'needs': task.bundle['plan'],
            'remaining_budget': task.budget(), 'accepted_existing_urls': list(task.catalog())[:30]})}]


def validate_current_plan(task, needs):
    view = task_view(task)
    if view['operating_mode'] != 'current_information' or not view['operating_clock_utc']:
        return
    now = datetime.fromisoformat(view['operating_clock_utc'].replace('Z', '+00:00'))
    months = {calendar.month_name[i].lower(): i for i in range(1, 13)}
    for need in needs:
        text = need['condition'].lower()
        if need['priority'] != 'critical' or not re.search(r'forecast|remainder|upcoming|on track', text) or re.search(r'historical|archived|then-current', text):
            continue
        for name, year in re.findall(r'(' + '|'.join(months) + r')\s+(20\d{2})', text):
            if (int(year), months[name]) < (now.year, now.month):
                from ForecastAgent.runtime.contracts import ContractError
                raise ContractError('past_window_forecast', 'condition',
                    'The operating clock is after this month. A critical acquisition need must collect actual source records, not forecast the remainder of a past month. Original dates are provenance only.')
