"""Small composition adapter; tools, channels and research retain separate owners."""
import copy
import hashlib
from ForecastAgent.runtime.contracts import check_schema
from ForecastAgent.tavily_research import canonical_url
from ForecastAgent.tools.intelligence_box.navigation import navigate
from ForecastAgent.channels.contracts import FIELD, POLICY, enabled, initialize, capabilities, code_identity, NETWORK_NAMES
from ForecastAgent.channels.official import _box, preflight, acquire
from ForecastAgent.tools.original_navigation import _original, _materialize, _table_window


def guide():
    return '''
Optional source capabilities: use intelligence_catalog to inspect exact source
parameters and curated issuer/authority profiles. API captures share native HTTP
slots; no extra search/model calls, pagination or retries are implicit. A known
official host is not target relevance, and empty records never prove absence.
Choose a path for the current obtainable gap, not every source in the catalog:
numeric observations -> intelligence_fetch with exact series/issuer/period;
European statistics -> Eurostat exact dimensions/time or ECB exact series/window;
weather -> NWS point lookup, then explicit grid forecast/hourly/station selection;
issuer facts -> SEC observed ten-digit CIK, concept/unit/period/filed identity;
NWS and SEC need a valid contact User-Agent; catalog availability is checked
against actual transport configuration. Forecasts are not station observations.
official index or issuer report directory -> intelligence_discover, then
intelligence_acquire_link with its task-owned capture_id and zero-based index;
Congress -> detail/actions/text index followed by intelligence_bill_text;
unstructured news -> bounded Tavily/Exa or GDELT leads, then read_sources.
Discovery captures are link inventories, not article bodies. GDELT/feeds do not
replace full text. GDELT cooldown and missing credentials are listed in the
catalog; choose another applicable source instead of retry loops. A source
contract is usable only for its entity, fiscal period, metric and event stage.
Use existing need_ids; research_gap_ids identify obtainable map gaps separately.
Preserve downloaded-but-unparsed files as captured_unparsed; no readable claim.
For saved originals use intelligence_outline, then intelligence_search and
intelligence_part. Supply exactly one saved url or capture_id. Select the target
PDF page, section or HTML row window; pair numerical values with headers/units.
Returned unit offsets are normalized navigation coordinates. Use the separately
returned native evidence handles for map bindings. Newly materialized views keep
original bytes/time and old parsed versions. Inspect current map/material hashes
after reading; stale handles cannot be reused. No probabilities inside these tools.
'''

def execute(task, name, args):
    if not enabled(task):
        raise ValueError('Native channel capabilities are not enabled for this task')
    initialize(task)
    from ForecastAgent.tools.capabilities import get
    check_schema(args, get(name).definition['function']['parameters'])
    args = copy.deepcopy(args)
    need_ids = args.pop('need_ids', [])
    preflight(task, name, args, need_ids)
    box = _box(task, need_ids)
    try:
        from ForecastAgent.channels.discovery import preflight as discovery_preflight
        discovery_preflight(task, name, args, box)
        if name in {'intelligence_outline', 'intelligence_search', 'intelligence_part'}:
            url, capture, raw = _original(task, args, box)
            options = {k: v for k, v in args.items() if k not in {'url', 'capture_id'}}
            result = navigate(capture, raw, name.removeprefix('intelligence_'), **options)
            if name == 'intelligence_part':
                original = task.bundle['pages'].get(url) or next((p['page'] for p in reversed(task.bundle.get('failed_captures', []))
                    if p.get('channel_capture_id') == args.get('capture_id')), None)
                header = _table_window(capture, raw, options, result)
                if header:
                    _materialize(task, url, header, original)
                    result['header_view'] = {k: header[k] for k in ('text', 'native_coordinates') if k in header}
                _materialize(task, url, result, original)
                if header and header.get('native_coordinates'):
                    # Row projection may have created a later parsed body version.
                    header['native_coordinates']['body_sha256'] = hashlib.sha256(
                        task.bundle['pages'][url]['content'].encode()).hexdigest()
                if header and header.get('native_coordinates') and result.get('evidence'):
                    from ForecastAgent.research_loop import state
                    material = state.catalog(task.bundle, task.cutoff)
                    h = header['native_coordinates']
                    refs = [s for s in material['spans'].values() if s['url'] == url and
                        s['start'] < h['native_end'] and s['end'] > h['native_start']]
                    ids = {r['evidence_id'] for r in result['evidence']}
                    result['evidence'] = (result['evidence'] + [s for s in refs if s['evidence_id'] not in ids])[:4]
            return result
        if name == 'intelligence_budget':
            return {'budget': task.budget(), 'authority': 'native_fetch_attempts', 'additional_allowance': 0}
        if name in NETWORK_NAMES:
            return acquire(task, name, args, need_ids, box)
        result = box.call(name, args)
        if name == 'intelligence_catalog':
            from ForecastAgent.channels.selection import enrich_catalog
            result = enrich_catalog(task, result)
        if name == 'intelligence_links':
            capture = task.bundle['channel_tools']['captures'].get(args['capture_id'])
            if not capture:
                raise ValueError('Choose this task saved capture')
            for entry in result.get('links', []):
                url = entry.get('url') if isinstance(entry, dict) else entry
                if isinstance(url, str):
                    from ForecastAgent.retrieval_sources import allowed_source
                    if allowed_source(url):
                        task.bundle['source_leads'].setdefault(canonical_url(url), {
                            'url': url, 'origin': 'page_link', 'parent_url': capture['request_url']})
            task.save()
        return result
    finally:
        box.close()
