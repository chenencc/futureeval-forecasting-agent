"""State-bound tool menus and source availability, without model or HTTP calls."""
import copy
from datetime import datetime, timezone

from ForecastAgent.channels.contracts import NETWORK_NAMES, enabled
from ForecastAgent.channels.discovery import is_index
from ForecastAgent.tools.intelligence_box.catalog import SOURCES
from ForecastAgent.tools.intelligence_box.discovery import ROUTES
from ForecastAgent.tools.intelligence_box.profiles import PROFILES
from ForecastAgent.channels.configuration import missing_configuration


def availability(task, source_id):
    source = SOURCES[source_id]
    missing = missing_configuration(source_id, source)
    ready_at = task.bundle.get('channel_tools', {}).get('provider_ready_at', {}).get(source_id)
    cooling = ready_at and datetime.fromisoformat(ready_at) > datetime.now(timezone.utc)
    status = ('current_mode_unavailable' if task.bundle['mode'] != 'live' or task.cutoff
              else 'configuration_required' if missing else 'cooldown' if cooling
              else 'budget_exhausted' if task.budget()['page_fetch_remaining'] <= 0
              else 'available')
    return {'status':status, 'configuration_names':missing, 'next_request_at_utc':ready_at,
            'budget_authority':'native_fetch_attempts', 'target_relevance_verified':False}


def enrich_catalog(task, result):
    result = copy.deepcopy(result)
    for row in result['sources']:
        row['availability'] = availability(task, row['id'])
        row['required_parameters'] = row.get('path_parameters', []) + (
            ['query'] if row['id'] == 'gdelt_news' else [])
        row['parameter_guidance'] = parameter_guidance(row['id'])
    result['native_usage'] = {
        'budget':task.budget(), 'discovery_requires_original_download':True,
        'selection_order':['reuse_saved_original', 'exact_official_adapter_or_index',
                           'targeted_search', 'free_body_capture', 'failed_primary_rescue'],
        'instruction':'Choose only applicable sources. Check entity, period, metric, '
            'unit and event stage before acquisition. Index leads and revised observations '
            'are not final event outcomes or historical publication vintages.'}
    return result


def parameter_guidance(source_id):
    """Expose source-specific selection without granting IDs or interpreting data."""
    if source_id == 'eurostat_data':
        return 'Exact dataset and JSON-encoded filters: include geo, metric/unit dimensions and bounded time selection. Missing cells are unknown, not zero.'
    if source_id == 'ecb_series':
        return 'Exact flow and complete series; supply startPeriod/endPeriod OR lastNObservations (1..100). Wildcards are rejected. Keep units/status columns.'
    if source_id == 'nws_point':
        return 'Use observed numeric latitude/longitude. Save returned office/grid and forecast/hourly/station URLs for subsequent explicit requests.'
    if source_id in {'nws_forecast', 'nws_hourly', 'nws_stations'}:
        return 'Copy office, x and y from the saved point response; do not guess a grid. Directory, forecast and hourly requests each consume one native HTTP slot.'
    if source_id == 'nws_observation':
        return 'Copy an exact station ID from an observed station directory or question rules. Latest observation is not a forecast or historical archive.'
    if source_id == 'nws_alerts':
        return 'Supply exactly one area OR point. Empty active alerts do not establish absence of a past or future event.'
    if source_id.startswith('sec_'):
        return 'Use a ten-digit CIK already observed in saved material or rules. Keep issuer, taxonomy, concept, unit, fiscal period, filed date and accession. GAAP does not replace non-GAAP.'
    return None


def filter_tools(task, tools):
    if not enabled(task):
        return tools
    result = []
    captures = task.bundle['channel_tools']['captures']
    known = {**task.catalog(), **task.bundle['pages']}
    routes = list(dict.fromkeys([r['url'] for r in ROUTES] +
        [p['url'] for p in PROFILES.values()] + list(known)))[:100]
    for original in tools:
        tool = copy.deepcopy(original)
        name = tool['function']['name']
        if name in NETWORK_NAMES and (task.bundle['mode'] != 'live' or task.cutoff
                or task.bundle.get('result') or task.bundle['control'].get('forced_close')
                or task.budget()['page_fetch_remaining'] <= 0):
            continue
        props = tool['function']['parameters']['properties']
        if name == 'intelligence_fetch':
            selectable = [s for s in SOURCES if availability(task, s)['status'] == 'available']
            if not selectable:
                continue
            props['source_id']['enum'] = selectable
        if name == 'intelligence_discover':
            props['url']['enum'] = routes
            props['profile_id']['enum'] = list(PROFILES)
        if name == 'intelligence_read':
            if not known:
                continue
            props['url']['enum'] = list(known)[:100]
        if name == 'intelligence_acquire_link':
            parents = [i for i,c in captures.items() if c['status'] == 'usable'
                       and is_index(c) and c['source_id'].startswith('discovery:')
                       and any(r.get('eligible') and r.get('target_kind') != 'sitemap'
                               for r in c['records'])]
            if not parents:
                continue
            props['capture_id']['enum'] = parents
        if name in {'intelligence_links', 'intelligence_tables', 'intelligence_provisions', 'intelligence_bill_text'}:
            if not captures:
                continue
            props['capture_id']['enum'] = list(captures)
        result.append(tool)
    return result
