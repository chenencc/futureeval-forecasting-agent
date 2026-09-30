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
TOOLS.insert(-1, tool("list_sources", "Inspect real source URLs and saved evidence; no network or search cost. Use this instead of guessing paths. Also lists Extract-eligible failed pages.", {}, []))
TOOLS.insert(-1, tool("audit_evidence", "Review ALL saved evidence before finishing. Check exact entity/form identity, whether quote entails entire claim, announcement versus effective time, observation availability, absence claims and source independence. Reject overbroad claims; never infer absence from failed search.",
    {"reviews": {"type": "array", "items": {"type": "object", "properties": {"evidence_id": STRING,
     "entity_matches": {"type": "boolean"}, "quote_supports_claim": {"type": "boolean"},
     "time_valid": {"type": "boolean"}, "reason": STRING}, "required": ["evidence_id", "entity_matches", "quote_supports_claim", "time_valid", "reason"]}}}, ["reviews"]))

