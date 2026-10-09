"""Prioritize observed public detail/data links under existing capture budgets."""
import re
from urllib.parse import urlsplit

from ForecastAgent.providers.financial import source_urls
from ForecastAgent.readers.material_structure import observable_url
from ForecastAgent.tavily_research import canonical_url


def recovery_plan(bundle, admission, *, limit=12):
    from ForecastAgent.intelligence.adapters import observed_api_candidates
    request = bundle["request"]
    candidates = {}
    rule_urls = set(source_urls(request.get("resolution_criteria", "") + "\n" + request.get("fine_print", "")))
    terms = set(re.findall(r"[a-z0-9]{4,}", request.get("question", "").lower()))
    def add(url, parent, origin, title=""):
        public = observable_url(url, parent)
        if not public:
            return
        host = (urlsplit(public).hostname or "").lower()
        if host == "metaculus.com" or host.endswith(".metaculus.com"):
            return
        parsed = urlsplit(public)
        resource = bool(re.search(r"\.(?:csv|json|xlsx|pdf)(?:$|\?)|output=csv|format=(?:csv|json)|/api/", public, re.I))
        if re.search(r"/(?:login|signin|subscribe|privacy|terms|contact)(?:/|$)", parsed.path, re.I):
            return
        key = canonical_url(public)
        overlap = len(terms & set(re.findall(r"[a-z0-9]{4,}", (public+" "+title).lower())))
        priority = 100 * (public in rule_urls) + 70 * resource + 8 * overlap
        if not priority and origin != "question_background":
            return
        old = candidates.get(key)
        row = {"url": public, "origin": origin, "parent_url": parent, "title": title[:200],
               "priority": priority, "resource_kind": "structured_or_document" if resource else "detail_page",
               "public_destination_verified": False, "metric_match_verified": False,
               "requires_http_reservation": True}
        if old is None or priority > old["priority"]:
            candidates[key] = row
    for field in ("resolution_criteria", "fine_print", "background"):
        for url in source_urls(request.get(field, "")):
            add(url, url, "question_" + field)
    admitted_urls = {r["url"] for r in admission["sources"] if r["action"] in {"admit", "duplicate_body"}}
    for parent, page in bundle.get("pages", {}).items():
        for api in observed_api_candidates(parent):
            add(api["url"], parent, "documented_public_api", api["observed_identifier"])
            candidate = candidates.get(canonical_url(api["url"]))
            if candidate:
                candidate.update(adapter=api["adapter"], observed_identifier=api["observed_identifier"],
                                 route_documented=True, priority=candidate["priority"] + 140)
        for url in source_urls(page.get("content", "")):
            add(url, parent, "saved_body_link")
        for link in page.get("links", []):
            if isinstance(link, str):
                add(link, parent, "saved_html_link")
            elif isinstance(link, dict):
                add(link.get("url", link.get("href", "")), parent, "saved_html_link", link.get("text", ""))
        for resource in page.get("observed_resources", []):
            if isinstance(resource, dict):
                add(resource.get("url", ""), parent, "observed_data_resource", resource.get("title", ""))
        for url in source_urls(page.get("final_url", "")):
            if url != parent:
                add(url, parent, "observed_redirect")
    admitted_urls = {canonical_url(url) for url in admitted_urls}
    available = [r for key, r in candidates.items() if key not in admitted_urls]
    available.sort(key=lambda row: (-row["priority"], row["url"]))
    return {"schema": "predictive_source_recovery_v1", "candidates": available[:limit],
            "truncated": len(available) > limit, "network_calls": 0, "budget_reset": False,
            "instruction": "Read through the existing safe capture executor and its durable HTTP/browser reservations. Do not invent paths or create new allowances."}


def read_candidate(candidate, capture):
    """The caller owns safe networking and durable reservations, including failures."""
    # `capture` must be the existing HTTP/browser budget executor; this tool
    # never supplies a second network client or an independent quota ledger.
    url = candidate["url"]
    if not observable_url(url, url):
        raise ValueError("Invalid public capture candidate")
    page = capture(url)
    from ForecastAgent.intelligence.admission import inspect
    diagnostic = inspect(page)
    result = {"url": url, "page": page, "body_diagnostics": diagnostic,
            "original_parent_url": candidate.get("parent_url"),
            "metric_match_verified": False, "budget_owned_by_capture_executor": True}
    if candidate.get("adapter") == "clinicaltrials_study_v2":
        import json
        from ForecastAgent.intelligence.adapters import clinical_study_view
        result["structured_view"] = clinical_study_view(json.loads(page["content"]), candidate["observed_identifier"])
    return result
