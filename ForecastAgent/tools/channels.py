"""Repository-owned capability catalog; availability is not a reliability score."""

CHANNELS = [
    {'id':'exa_search','kind':'discovery','tools':['search_exa'],'formats':['search_results'],
     'cost':'Exa account credits; response estimate saved, not a billing balance',
     'credentials':['EXA_API_KEY (Actions secret EXA_API)'],
     'limits':'Optional one metadata-only auto search per new task; failures count, no retries. Existing ledgers remain disabled.',
     'temporal_support':'Publication bounds only, not archived body versions. Historical unknown dates are quarantined.',
     'availability':'implemented'},
    {'id':'dated_observations','kind':'structured_data','tools':['list_dated_datasets','collect_dataset'],
     'formats':['json_rows'],'cost':'No paid search call','credentials':['SEC_USER_AGENT for SEC'],
     'limits':'Each physical HTTP attempt shares the existing eight-fetch ledger.',
     'temporal_support':'Date-bounded current vintage only; historical_strict refuses.', 'availability':'implemented'},
    {'id':'historical_archive','kind':'download','tools':['collect_archive'],'formats':['archived_document'],
     'cost':'No paid search call','credentials':[], 'limits':'Two physical HTTP attempts per lookup/replay; exact discovered URLs only.',
     'temporal_support':'Archive timestamp strictly before cutoff; missing captures and redirects fail closed.', 'availability':'implemented'},
    {"id": "tavily_basic", "kind": "discovery", "tools": ["search_tavily"],
     "formats": ["search_results"], "cost": "Tavily credits", "credentials": ["TAVILY_API_KEY"],
     "limits": "Frozen per-task budget: three for original profiles; collection_v2 defaults to three with a five-attempt ceiling. Failures count.",
     "temporal_support": "Publication filters only; not archived page versions.", "availability": "implemented"},
    {"id": "public_http", "kind": "download", "tools": ["fetch_page", "fetch_pages"],
     "formats": ["html", "text", "pdf_text", "csv", "json", "rss", "atom"], "cost": "No paid provider call",
     "credentials": [], "limits": "Eight new fetch attempts; discovered public URLs only; bounded bytes.",
     "temporal_support": "Current capture, or an imported pre-cutoff local snapshot.", "availability": "implemented"},
    {"id": "yahoo_daily", "kind": "structured_data", "tools": ["fetch_page", "fetch_pages"],
     "formats": ["daily_series"], "cost": "No paid provider call", "credentials": [],
     "url_pattern": "https://finance.yahoo.com/quote/{symbol}/history/",
     "limits": "Recognized source URL required; shares the eight-fetch budget.",
     "temporal_support": "Cutoff day withheld; current-vintage revisions remain possible.", "availability": "implemented"},
    {"id": "alfred_vintage", "kind": "structured_data", "tools": ["fetch_page", "fetch_pages"],
     "formats": ["vintage_series"], "cost": "No paid provider call", "credentials": [],
     "url_pattern": "https://alfred.stlouisfed.org/series/{series_id}",
     "limits": "Recognized source URL required; requested vintage must be confirmed; shares fetch budget.",
     "temporal_support": "Vintage before the cutoff day.", "availability": "implemented"},
    {"id": "tavily_extract_basic", "kind": "rescue", "tools": ["extract_failed_pages"],
     "formats": ["vendor_markdown"], "cost": "Tavily credits", "credentials": ["TAVILY_API_KEY"],
     "limits": "One batch, up to five accepted important URLs whose free fetch failed.",
     "temporal_support": "Current vendor capture; unavailable in historical strict mode.", "availability": "implemented"},
    {"id": "saved_documents", "kind": "local_reader", "tools": ["list_documents", "read_document", "search_saved_text", "record_excerpt", "record_quote", "find_passages"],
     "formats": ["text", "pdf_page", "csv_row", "json", "series_row"], "cost": "No network or provider call",
     "credentials": [], "limits": "Only saved text; bounded output; truncation is reported.",
     "temporal_support": "Inherits the saved source's temporal status.", "availability": "implemented"},
    {"id": "polymarket_gamma", "kind": "market_discovery", "tools": ['collect_polymarket', 'read_market_snapshot'],
     "formats": ["market_candidates"], "cost": "Public HTTP", "credentials": [],
     "limits": "Active discovery; explicit pages one to three; each call shares the eight HTTP attempt cap; no executable quotes or equivalence verdict.",
     "temporal_support": "Current snapshots only; historical modes refuse requests.", "availability": "implemented"},
    {'id': 'official_government', 'kind': 'structured_data', 'tools': ['list_official_datasets', 'collect_official'],
     'formats': ['series_rows', 'fiscal_rows', 'document_leads'], 'cost': 'No paid provider call', 'credentials': [],
     'limits': 'BLS CPI/unemployment/payrolls, Treasury debt, Federal Register; bounded explicit pages; shares eight HTTP attempts.',
     'temporal_support': 'Current captures only; no historical release-vintage claim.', 'availability': 'implemented'},
    {'id': 'incremental_refresh', 'kind': 'update', 'tools': ['refresh_sources'],
     'formats': ['version_changes'], 'cost': 'No paid provider call', 'credentials': [],
     'limits': 'Saved live pages only; initial eight HTTP attempts before completion; afterward three per UTC day and 24 lifetime update attempts. Conditional 304 retains old bytes and capture times.',
     'temporal_support': 'New live captures with preserved previous versions.', 'availability': 'implemented'},
    {'id': 'collection_acceptance', 'kind': 'local_check', 'tools': ['collection_acceptance', 'collection_checkpoint', 'select_sources', 'record_channel_decision','plan_channels'],
     'formats': ['integrity_report'], 'cost': 'No network', 'credentials': [],
     'limits': 'Integrity, coordinates and gaps only; no reliability or forecast score.',
     'temporal_support': 'Reports stored metadata limitations.', 'availability': 'implemented'},
]


def channel_catalog():
    from copy import deepcopy
    channels = deepcopy(CHANNELS)
    return {"version": 3, "channels": channels,
            "policy": "Catalog descriptions do not grant URLs, credentials, extra calls or historical eligibility."}


def tool_result(name, data, budget, *, error=None):
    """Stable agent-facing result envelope; native execute callers stay compatible."""
    items = data.get("items", []) if isinstance(data, dict) else []
    failed = error is not None or bool(items) and not any(item.get("ok") for item in items)
    partial = bool(items) and any(not item.get("ok") for item in items) and not failed
    status = "failed" if failed else "partial" if partial else "completed"
    if name.startswith('intelligence_') and isinstance(data, dict):
        if data.get('status') in {'configuration_required', 'failed', 'empty'}:
            status = data['status']
            failed = status != 'empty'
    channel_ids = [c["id"] for c in CHANNELS if name in c["tools"]]
    if name.startswith('intelligence_'):
        from ForecastAgent.tools.capabilities import get
        capability = get(name)
        channel_ids = ['native_official_channels' if capability.kind == 'channel' else 'original_navigation']
    return {"version": "tool_result_v1", "tool": name, "ok": not failed,
            "status": status,
            "data": data, "error": error, "budget_remaining": budget,
            "provenance": {"channel_ids": channel_ids,
                           "truth_verified": False}}
