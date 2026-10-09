"""Small official public-data adapters derived from observed source identifiers."""
import re
from urllib.parse import urlsplit


def observed_api_candidates(url):
    """Use a documented adapter, not a model-invented endpoint.

    Route reference: US NLM Technical Bulletin, July-August 2025,
    Does Screen Scraping ClinicalTrials.gov Work?
    """
    parts = urlsplit(url)
    match = re.fullmatch(r"/study/(NCT\d{8})/?", parts.path, re.I)
    if parts.scheme == "https" and parts.hostname == "clinicaltrials.gov" and match:
        ident = match.group(1).upper()
        return [{"url": "https://clinicaltrials.gov/api/v2/studies/" + ident,
                 "adapter": "clinicaltrials_study_v2", "observed_identifier": ident,
                 "parent_url": url, "route_documented": True,
                 "source_reference": "https://www.nlm.nih.gov/pubs/techbull/ja25/ja25_clinical_trials_screen-scraping.html",
                 "metric_match_verified": False}]
    return []


def clinical_study_view(data, expected_id):
    """Preserve actual study status and intervention names; never infer a count."""
    protocol = data.get("protocolSection", {})
    identity = protocol.get("identificationModule", {})
    if identity.get("nctId") != expected_id:
        raise ValueError("Official API study identity differs from the observed identifier")
    status = protocol.get("statusModule", {})
    interventions = protocol.get("armsInterventionsModule", {}).get("interventions", [])
    return {"nct_id": expected_id, "title": identity.get("briefTitle"),
            "overall_status": status.get("overallStatus"),
            "last_update": status.get("lastUpdatePostDateStruct"),
            "interventions": [{"name": row.get("name"), "type": row.get("type"),
                               "other_names": row.get("otherNames", [])} for row in interventions],
            "exact_intervention_match_verified": False, "population_count_verified": False}
