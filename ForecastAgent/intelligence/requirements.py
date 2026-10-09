"""Keep predictive inputs separate from future resolution observations."""
import copy
from datetime import datetime

from ForecastAgent.providers.financial import source_urls

PURPOSES = {"definition", "current_baseline", "history", "driver", "future_outcome"}


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Use an explicit timezone for an observation clock")
    return parsed

INSTRUCTIONS = """Collect information available at the operating clock. First identify
the exact entity, metric, units, official source, event stage and resolution window.
Prioritize current baselines, comparable history and forward-looking drivers.
A future realized outcome is normally unknown: retain it as expected_unknown,
not a mandatory missing document. Failed access to an observable baseline remains
a real capture gap. Never infer No from a missing record or use net approval as
approval, requests as approvals, or a related event as the target event.
Prefer observed detail pages, table/CSV/JSON links and dated datasets over site
menus or generic biographies. Deduplicate bodies while preserving all provenance.
These are acquisition instructions, not permission to estimate probabilities."""


def contract(request, *, clock_utc=None):
    if not request.get("resolution_criteria"):
        raise ValueError("Exact resolution criteria are required")
    clock = clock_utc or request.get("as_of_utc")
    if not clock:
        raise ValueError("Freeze an explicit operating clock")
    timestamp(clock)
    fields = ("id", "post_id", "question", "question_type", "resolution_criteria", "fine_print",
              "background", "options", "scaling", "scheduled_resolve_time", "scheduled_close_time")
    original = {k: copy.deepcopy(request[k]) for k in fields if k in request}
    return {"schema": "predictive_information_contract_v1", "operating_clock_utc": clock,
            "original_question": original,
            "target_fields": ["entity", "metric", "units", "source", "event_stage", "time_window"],
            "observable_inputs": ["current_baseline", "history", "driver", "definition"],
            "future_outcome_policy": "Expected unknown unless a qualifying outcome is already observable.",
            "rule_source_urls": source_urls(request["resolution_criteria"] + "\n" + request.get("fine_print", "")),
            "instructions": INSTRUCTIONS, "semantic_binding_verified": False}


def classify_need(need, clock_utc):
    """Explicit declarations only; never guess future observability from an ID."""
    operating_clock = timestamp(clock_utc)
    purpose = need.get("purpose")
    if purpose not in PURPOSES:
        return {"status": "needs_observability_declaration", "blocking_capture_gap": None,
                "reason": "Declare definition, current_baseline, history, driver or future_outcome."}
    if purpose == "future_outcome":
        after = need.get("available_after_utc")
        if not after or timestamp(after) <= operating_clock:
            raise ValueError("An expected-unknown outcome requires an explicit later observation time")
        return {"status": "expected_unknown", "blocking_capture_gap": False,
                "available_after_utc": after, "agent_declared": True, "truth_verified": False}
    return {"status": "observable_input", "blocking_capture_gap": True,
            "purpose": purpose, "agent_declared": True, "truth_verified": False}
