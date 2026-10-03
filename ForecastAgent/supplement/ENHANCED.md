# Enhanced supplemental acquisition candidate

This development candidate keeps the original collector and Mercury probability
questions. It adds four contracts between acquisition and analysis:

1. **Rule-driven gap plans.** Plan from exact question/rules, saved search hits,
   links, source leads and body diagnostics. Retain rule primary URLs. No labels,
   predictions or outcome-selected source fixtures drive the automatic planner.
   Missing coverage is uncertainty, never proof of a negative outcome.
2. **Shared lifetime reservations.** Maximum three Tavily basic requests and one
   Exa request, counting original acquisition plus supplemental reservations.
   Free requests have cumulative ceilings of 16 HTTP and six browser attempts.
   Supply all nonoverlapping prior repair journals. Existing over-cap usage is
   preserved and prohibits additional requests. Failure and interrupted requests
   consume quota. Search is optional, only when observed leads are unavailable.
   The existing collector remains responsible for its own model budget; this
   layer makes no additional acquisition model requests.
3. **Body and coverage acceptance.** Preserve every response, including rejected
   privacy/promotion shells. Readable wrong-month text can remain context but
   cannot close a target-month gap. Month and lexical overlap are diagnostics,
   not verified event dates or exact metric validation. Preserve explicit gaps
   for subsequent analysis, including blocked URLs.
4. **Bounded reading priorities.** Persist source hashes and exact character
   offsets. Reserve half of the available Mercury window for ranked passages;
   use the retained selector for remaining breadth. Emit selected/omitted IDs.
   Oversized evidence may still be omitted and is reported, never silently
   represented as complete. Keep the same probability questions and routing.

## Run in a Linux GitHub Actions worker

Install `ForecastAgent/requirements-supplement.txt` and run
`python -m playwright install --with-deps chromium` for browser fallback.
Use a **new candidate output path**, preserving parent journals:

```bash
python -m ForecastAgent.supplement.enhanced \
  --input saved/analysis-input.json --prior saved/supplement.json \
  --output candidate/task --network --search --analyze
```

Search credentials use `TAVILY_API_KEY` and `EXA_API_KEY`; Mercury uses
`OPENROUTER_API_KEY`. Omit `--search` for free observed-URL repair. Omit
`--network --analyze` for offline planning and window preparation. No submission
endpoint is called by this command. It uses the existing public-only HTTP and
browser executors, without bypassing access controls.

Artifacts: `identity.json`, durable `state.json`, `plan.json`, raw captures,
`analysis-input.json`, `reading-hints.json`, and usage/report files. Do not
restart budgets by deleting candidate state. Imports into a future collector
must include this supplemental journal in lifetime accounting.

The official workflow stays pinned to release **v1.0.1**. This is an opt-in
development candidate, pending live Linux worker acceptance before promotion.

## Offline acceptance

```bash
python -m unittest ForecastAgent.tests.test_enhanced_supplement
python -m ForecastAgent.supplement.validate_enhanced \
  --parent saved-six --output validation
```

The paired window audit keeps identical accepted original bodies. It reports
raw preservation, shell exclusions, date coverage gaps and selected/omitted
priority passages. It does not call models/search or claim forecasting gains.
