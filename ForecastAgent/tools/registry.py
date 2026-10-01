"""Structured tool schemas exposed to Ultra; credentials and budgets are not model arguments."""
def tool(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}

STRING = {"type": "string"}
TOOLS = [
    tool("plan_evidence", "First identify every necessary resolution condition, boundaries, timing, official source, current status and reference-class needs. No forecast.",
         {"needs": {"type": "array", "items": {"type": "object", "properties": {
             "id": STRING, "condition": STRING, "priority": {"type": "string", "enum": ["critical", "useful"]},
             "expected_source": STRING, "query": STRING}, "required": ["id", "condition", "priority", "expected_source", "query"]}}}, ["needs"]),
    tool("search_tavily", "Spend one of at most THREE basic search attempts. Only search for important missing evidence or unresolved contradictions. Cannot override date or depth.",
         {"query": STRING, "need_ids": {"type": "array", "items": STRING}, "reason": STRING,
          "topic": {"type": "string", "enum": ["general", "news", "finance"]},
          "include_domains": {"type": "array", "items": STRING, "maxItems": 10},
          "include_domains_mode": {"type": "string", "enum": ["prefer", "restrict"]},
          "exact_match": {"type": "boolean"}}, ["query", "need_ids", "reason", "topic", "include_domains", "include_domains_mode", "exact_match"]),
    tool("fetch_page", "Read a discovered URL using free HTTP fetching. Failed reads are logged. Historical strict mode refuses today's pages.", {"url": STRING}, ["url"]),
    tool("fetch_pages", "Read up to five REAL URLs from the source catalog in one turn. Caches, PDF parsing and financial data adapters are automatic. Each new URL consumes one of eight free fetch attempts.",
         {"urls": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 5}}, ["urls"]),
    tool("extract_failed_pages", "One basic Extract rescue batch per task, up to five URLs. Only important accepted pages whose free fetch failed; batch candidates together. Never historical strict.",
         {"urls": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 5}, "need_ids": {"type": "array", "items": STRING}, "reason": STRING}, ["urls", "need_ids", "reason"]),
    tool("record_evidence", "Extract a concrete fact with an exact supporting quote from a fetched page. Explain quality dimensions separately; identify the ORIGINAL data provider for independence.",
         {"url": STRING, "claim": STRING, "quote": STRING, "need_ids": {"type": "array", "items": STRING},
          "stance": {"type": "string", "enum": ["supports", "opposes", "neutral"]},
          "original_source": STRING, "event_time": STRING,
          "quality": {"type": "object", "properties": {k: STRING for k in ["authority", "directness", "relevance", "verifiability"]}, "required": ["authority", "directness", "relevance", "verifiability"]}},
         ["url", "claim", "quote", "need_ids", "stance", "original_source", "event_time", "quality"]),
    tool("finish_retrieval", "Finish without predicting. State gaps and conflicts explicitly; sufficient requires every critical need covered and no unresolved conflicts.",
         {"status": {"type": "string", "enum": ["sufficient", "partial", "conflicted", "failed"]},
          "gaps": {"type": "array", "items": STRING}, "conflicts": {"type": "array", "items": STRING}, "summary": STRING},
         ["status", "gaps", "conflicts", "summary"]),
]
TOOLS.insert(-1, tool("load_research_skill", "Load a named research skill from the catalog. No network, search or forecast; instructions cannot override program limits.", {"name": STRING}, ["name"]))
TOOLS.insert(-1, tool('search_exa', 'Optional independent discovery for a critical gap after Tavily. Frozen maximum ONE attempt per task; failures count. Metadata only, no paid content or retries. Domain filters restrict results. Dates are program-owned, not archive proof.',
    {'query':STRING, 'need_ids':{'type':'array','items':STRING}, 'reason':STRING,
     'search_role':{'type':'string','enum':['crosscheck','gap','recent','official_gap']},
     'category':{'type':'string','enum':['general','news','publication','financial report']},
     'include_domains':{'type':'array','items':STRING,'maxItems':10}},
    ['query','need_ids','reason','search_role','category','include_domains']))
PLAN_SCHEMA = TOOLS[0]["function"]["parameters"]
PLAN_SCHEMA["properties"]["entity_card"] = {"type": "object", "properties": {k: STRING for k in ["subject", "identity_checks", "required_form", "announcement_window", "effective_vs_announcement"]}, "required": ["subject", "identity_checks", "required_form", "announcement_window", "effective_vs_announcement"]}
PLAN_SCHEMA["required"].append("entity_card")
FETCH_SCHEMA = next(t["function"]["parameters"] for t in TOOLS if t["function"]["name"] == "fetch_page")
FETCH_SCHEMA["properties"]["start_char"] = {"type": "integer", "minimum": 0}
EVIDENCE_PROPERTIES = next(t["function"]["parameters"]["properties"] for t in TOOLS if t["function"]["name"] == "record_evidence")
EVIDENCE_PROPERTIES.update(time_role={"type": "string", "enum": ["announcement", "observation", "definition", "future_schedule"]},
                           announcement_at=STRING, effective_at=STRING)
TOOLS.insert(-1, tool("record_evidence_batch", "Bank up to eight independent concrete facts in one turn; exact saved quotes and dates remain mandatory. Each item is validated separately.",
    {"items": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "object", "properties": EVIDENCE_PROPERTIES,
     "required": [k for k in EVIDENCE_PROPERTIES if k not in {"time_role", "announcement_at", "effective_at"}]}}}, ["items"]))
TOOLS.insert(-1, tool("list_sources", "Inspect accepted URLs and Extract candidates without search. Selected/question/search leads precede captured links. Page through the catalog or filter by exact parent_url.",
    {'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 120}, 'parent_url': STRING}, []))
TOOLS.insert(-1, tool("audit_evidence", "Review ALL saved evidence before finishing. Check exact entity/form identity, whether quote entails entire claim, announcement versus effective time, observation availability, absence claims and source independence. Reject overbroad claims; never infer absence from failed search.",
    {"reviews": {"type": "array", "items": {"type": "object", "properties": {"evidence_id": STRING,
     "entity_matches": {"type": "boolean"}, "quote_supports_claim": {"type": "boolean"},
     "time_valid": {"type": "boolean"}, "reason": STRING}, "required": ["evidence_id", "entity_matches", "quote_supports_claim", "time_valid", "reason"]}}}, ["reviews"]))

LOCAL_TOOLS = [
    tool("list_channels", "Inspect implemented acquisition channels, formats, credential names and limits. No network.", {}, []),
    tool("list_documents", "List saved document/page/row metadata with one-based document indices. No network.",
         {"url": STRING, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, []),
    tool("read_document", "Read saved content or a saved PDF page/data row by document index. Character offsets refer to saved parsed text. No fetch.",
         {"url": STRING, "document_index": {"type": "integer", "minimum": 1}, "start_char": {"type": "integer", "minimum": 0},
          "max_chars": {"type": "integer", "minimum": 1, "maximum": 18000}}, ["url"]),
    tool("search_saved_text", "Case-sensitive literal search across saved documents; return exact match coordinates and bounded context. No web search.",
         {"query": STRING, "url": STRING, "offset": {"type": "integer", "minimum": 0},
          "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ["query"]),
    tool("record_excerpt", "Save at most 4000 characters. Copy excerpt_args from search_saved_text and add need_ids; never estimate offsets. Prefer record_quote for a copied passage. No truth verdict.",
         {"url": STRING, "document_index": {"type": "integer", "minimum": 1},
          "start_char": {"type": "integer", "minimum": 0}, "end_char": {"type": "integer", "minimum": 1},
          "need_ids": {"type": "array", "items": STRING}}, ["url", "start_char", "end_char", "need_ids"]),
    tool('record_quote', 'Copy an exact passage from saved text; the program computes coordinates and preserves provenance. No search or model call. Repeated quotes require occurrence_index.',
         {'url': STRING, 'quote': {'type': 'string', 'maxLength': 4000},
          'document_index': {'type': 'integer', 'minimum': 1}, 'occurrence_index': {'type': 'integer', 'minimum': 1},
          'need_ids': {'type': 'array', 'items': STRING}}, ['url', 'quote', 'need_ids']),
    tool('collection_checkpoint', 'Inspect missing acquisition work and recommended tools before searching again or finishing. No network; does not verify truth.', {}, []),
    tool('find_passages', 'Find bounded saved-text windows matching multiple query words without case sensitivity. Copy excerpt_args to record_excerpt or copy a shorter unique quote. No network or truth check.',
         {'query': STRING, 'url': STRING, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10}}, ['query']),
    tool('select_sources', 'Select one to ten accepted source URLs for reading and associate needs. No network; preserves all unselected captured links separately.',
         {'urls': {'type': 'array', 'items': STRING, 'minItems': 1, 'maxItems': 10},
          'need_ids': {'type': 'array', 'items': STRING}, 'reason': STRING}, ['urls', 'need_ids', 'reason']),
    tool('record_channel_decision', 'Explain why a channel was deferred or is not applicable. This records acquisition planning only, never a relevance or truth verdict.',
         {'channel': {'type': 'string', 'enum': ['polymarket_gamma', 'tavily_extract_basic', 'official_government']},
          'decision': {'type': 'string', 'enum': ['deferred', 'not_applicable']}, 'reason': STRING}, ['channel', 'decision', 'reason']),
    tool("finish_collection", "Finish acquisition and export all saved material even without excerpts or audit. State unread items and gaps. Never assert truth or predict.",
         {"gaps": {"type": "array", "items": STRING}}, ["gaps"]),
]
TOOLS.extend(LOCAL_TOOLS)
TOOLS.extend([
    tool('list_official_datasets', 'Inspect supported government datasets, exact series identifiers, units and temporal limits. No network.', {}, []),
    tool('collect_official', 'Capture a supported government dataset. One of the shared eight free HTTP attempts; live mode only. Cache is reused.',
         {'dataset': {'type': 'string', 'enum': ['bls_cpi', 'bls_unemployment', 'bls_payrolls', 'treasury_debt', 'federal_register']},
          'query': STRING, 'page': {'type': 'integer', 'minimum': 1, 'maximum': 3},
          'need_ids': {'type': 'array', 'items': STRING}}, ['dataset', 'need_ids']),
    tool('collect_polymarket', 'Save active Gamma event/child-contract candidates, rules and display prices separately. No equivalence verdict or edge. Shares eight free HTTP attempts; current data only.',
         {'query': STRING, 'page': {'type': 'integer', 'minimum': 1, 'maximum': 3}, 'refresh': {'type': 'boolean'},
          'need_ids': {'type': 'array', 'items': STRING}}, ['query', 'need_ids']),
    tool('read_market_snapshot', 'Read saved Gamma snapshot metadata or a specific child contract without network calls. Market rules and prices remain unverified signals.',
         {'snapshot_id': STRING, 'market_id': STRING, 'offset': {'type': 'integer', 'minimum': 0},
          'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, ['snapshot_id']),
    tool('refresh_sources', 'Refresh saved sources in the SAME ledger: eight initial HTTP attempts before completion; after completion three per UTC day and 24 lifetime updates. Never renew search budget.',
         {'urls': {'type': 'array', 'items': STRING, 'minItems': 1, 'maxItems': 5}}, ['urls']),
    tool('collection_acceptance', 'Inspect raw response integrity, excerpt coordinates, budget and acquisition gaps. Mechanical checks only; no truth verification.', {}, []),
])
COLLECTION_TOOLS = [t for t in TOOLS if t["function"]["name"] not in
                    {"record_evidence", "record_evidence_batch", "audit_evidence", "finish_retrieval"}]
COLLECTION_TOOLS.extend([
    tool('read_sources','Batch up to four accepted source reads and locate complete paragraphs for up to eight acquisition queries. Free fetch budgets still apply per URL. No semantic verdict.',
         {'urls':{'type':'array','items':STRING,'maxItems':4},'queries':{'type':'array','maxItems':8,'minItems':1,'items':{'type':'object','properties':{'query':STRING,'url':STRING,'need_ids':{'type':'array','items':STRING}},'required':['query','need_ids']}}},['urls','queries']),
    tool('record_excerpts','Bank one to eight passages by exact passage_id from read_sources and need_ids. Do not enter character offsets. IDs are checked against saved source versions.',
         {'items':{'type':'array','minItems':1,'maxItems':8,'items':{'type':'object','properties':{'passage_id':STRING,'need_ids':{'type':'array','items':STRING}},'required':['passage_id','need_ids'],'additionalProperties':False}}},['items']),
    tool('list_dated_datasets','List supported date-bounded data adapters, units and temporal limitations. No network.',{},[]),
    tool('collect_dataset','Capture structured observations. Shares eight HTTP attempts. Current provider vintage, not a verified historical snapshot. Historical_strict refuses. SEC requires configured contact and a discovered CIK.',
         {'dataset':{'type':'string','enum':['binance_daily','north_atlantic_sst','sec_issuers','sec_submissions']},
          'start_date':STRING,'end_date':STRING,'query':STRING,'cik':STRING,'submission_file':STRING,'forms':{'type':'array','items':STRING,'maxItems':5},
          'need_ids':{'type':'array','items':STRING}},['dataset','need_ids']),
    tool('collect_archive','Obtain an exact URL pre-cutoff Wayback capture. Two separately reserved shared HTTP attempts; redirects or missing captures fail closed. Only source catalog URLs.',{'url':STRING},['url']),
    tool('read_dataset_rows','Read saved structured rows without network calls; current-vintage warning remains attached. Use pagination for large reference histories.',
         {'url':STRING,'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':100}},['url'])
])
for entry in COLLECTION_TOOLS:
    if entry['function']['name']=='search_tavily':
        entry['function']['parameters']['properties']['search_role']={'type':'string','enum':['primary','crosscheck','gap','recent','official_gap']}
COLLECTION_TOOLS.append(tool('plan_channels','Use catalog channel IDs, NOT tool names: dated_observations for collect_dataset, historical_archive for collect_archive, public_http for reading, tavily_basic for primary discovery, exa_search for supplemental discovery. Each row needs channel, need_ids, expected_http_attempts (0-8), reason. Estimates do not reserve calls. No network.',
    {'channels':{'type':'array','minItems':1,'maxItems':8,'items':{'type':'object','properties':{
        'channel':STRING,'need_ids':{'type':'array','items':STRING},'expected_http_attempts':{'type':'integer','minimum':0,'maximum':8},
        'reason':STRING},'required':['channel','need_ids','expected_http_attempts','reason'],'additionalProperties':False}}},['channels']))
for entry in COLLECTION_TOOLS:
    if entry['function']['name']=='collect_official':
        entry['function']['parameters']['properties'].update(start_date=STRING,end_date=STRING)
        entry['function']['description']+=' Optional paired start_date/end_date filters; BLS dates select observation months, not release dates. Inspect saved row pagination and gaps.'
