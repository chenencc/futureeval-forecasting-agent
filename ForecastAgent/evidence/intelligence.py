"""Export captured material without a truth verdict or a forecast."""
import json
from pathlib import Path
from ForecastAgent.tavily_research import canonical_url
from ForecastAgent.evidence.acceptance import collection_acceptance
from ForecastAgent.evidence.temporal import temporal_report


def intelligence_package(bundle):
    sources = dict(bundle.get("source_leads", {}))
    for search in bundle.get("searches", []) + bundle.get('exa_searches', []):
        for hit in search.get("results", []):
            sources.setdefault(canonical_url(hit["url"]), hit)
    return {"schema": "intelligence_package_v1", "request": bundle["request"],
            "request_hash": bundle["request_hash"], "mode": bundle["mode"],
            'collection_temporal_policy':bundle.get('collection_temporal_policy'),
            'temporal_policy_amendments':bundle.get('temporal_policy_amendments',[]),
            'result_history':bundle.get('result_history',[]),
            "collection_result": bundle.get("result"), "plan": bundle.get("plan"),
            "sources": list(sources.values()), "searches": bundle.get("searches", []),
            'exa_searches':bundle.get('exa_searches',[]),
            'budget_amendments':bundle.get('budget_amendments',[]),
            'selected_sources': bundle.get('selected_sources', {}), 'channel_decisions': bundle.get('channel_decisions', {}),
            'execution_versions': bundle.get('execution_versions', []),
            'acquisition_limits': bundle.get('acquisition_limits', {'tavily_basic':3}),
            'search_policy':bundle.get('search_policy', {}),
            'historical_body_policy':bundle.get('historical_body_policy'),
            'passages':bundle.get('passages',{}),
            'passage_dispositions':bundle.get('passage_dispositions',{}),
            'debug_resumptions':bundle.get('debug_resumptions',[]),
            'data_raw_responses':bundle.get('data_raw_responses',[]),
            'channel_plan':bundle.get('channel_plan',[]),'context_projections':bundle.get('context_projections',[]),
            'cache_events':bundle.get('cache_events',[]),'failed_captures':bundle.get('failed_captures',[]),
            'model_attempts': bundle.get('model_attempts', []),
            'step_attempts': bundle.get('step_attempts', []),
            'sessions':bundle.get('sessions', []), 'session_state':bundle.get('session_state'),
            'progress':bundle.get('progress', {}),
            'messages': bundle.get('messages', []), 'transcript': bundle.get('transcript', []),
            'temporal_provenance': temporal_report(bundle),
            "pages": bundle.get("pages", {}), "excerpts": bundle.get("excerpts", []),
            "page_history": bundle.get('page_history', {}), "updates": bundle.get('updates', []),
            "market_snapshots": bundle.get('market_snapshots', {}), "acceptance": collection_acceptance(bundle),
            "fetch_attempts": bundle.get("fetch_attempts", []), "extract_attempts": bundle.get("extract_attempts", []),
            'update_attempts': bundle.get('update_attempts', []),
            "quarantine": bundle.get("quarantine", []), "channel_catalog": bundle.get("channel_catalog"),
            "truth_verified": False, "out_of_sample": False,
            "limitations": ["Quotes are located in saved text, not independently fact-checked.",
                            bundle.get("temporal_warning", "Historical availability must be established separately.")],
            "resources": {"tavily_basic_attempts": len(bundle.get("searches", [])),
                          'exa_search_attempts':len(bundle.get('exa_searches',[])),
                          'model_http_attempts': len(bundle.get('model_attempts', [])),
                          "free_fetch_attempts": len(bundle.get("fetch_attempts", [])),
                          'update_http_attempts': len(bundle.get('update_attempts', [])),
                          "extract_batches": len(bundle.get("extract_attempts", []))}}


def export_intelligence(bundle, directory):
    path = Path(directory) / "intelligence.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(intelligence_package(bundle), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return str(path)
