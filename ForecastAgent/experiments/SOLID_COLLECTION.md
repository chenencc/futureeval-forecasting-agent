# Expanded Raw Collection Pilot

This is an opt-in development experiment, separate from the immutable production
release. The operator authorized fresh per-task ledgers and expanded capacities.
Old acquisitions and provider journals remain preserved. Provider account quotas
are external and are never reset by this program.

## Pipeline and budgets

Each case starts with the original question and resolution rules only. No saved
search results, captures, forecasts or separate evaluation labels are imported.
Current information is permitted; historical question text may contain edits.
This is a collection diagnostic, not a clean historical forecasting backtest.

| Resource | Lifetime cap per task |
| --- | --- |
| Tavily basic search | 6 |
| Exa search | 2, at least one initial attempt required |
| Initial free source calls | 32 |
| Combined initial and supplement free HTTP calls | 64 |
| Basic Extract rescue | 2 batches of up to 5 URLs |
| Supplement browser renders | 12, each with the existing 25-request ceiling |
| Model HTTP | 30 per dispatch, 90 lifetime |
| Productive model decisions | 24 per dispatch |
| Transport failure allowance | 6 per dispatch |
| Initial collection dispatch duration | 1,500 seconds |

Caps are ceilings, not spending targets. Existing no-progress stops, deduplication,
public URL checks and failed-attempt accounting remain active. Two consecutive
Ultra service failures select Super within the dispatch. Credential, request and
account quota errors do not select another model. Model fallback never replenishes
the task ledger.

The pilot covers binary, numeric, multiple choice, date and discrete questions.
Capture runs first, followed by independent HTTP/browser repair. No probability
analysis or forecast submission occurs. The experiment produces a full raw bundle,
original acquisition journal, independent repair journal and mechanical quality
inventory. Source associations are diagnostics, not verified relevance or truth.
Blocked content remains in the original or excluded capture archive.
Repair follows observed, relevant detail links for at most two additional link
levels under the same cumulative HTTP/render ceilings. It never invents paths or
creates a separate allowance for discovery. Depth and remaining candidates are
persisted for restart inspection.

## Running and resuming

Use the registered `Checked handoff paired forty snapshot experiment` workflow
with `experiment=solid_collection`. It delegates to the reusable expanded
collection workflow on the same commit. An empty `resume_run` creates an independent fresh
experiment. For continuation, supply the exact prior run; all five per-case
artifacts are restored. Rerunning a job without that parent is prohibited. A
changed request, experiment identity, capacity or runner identity fails closed.

The same experiment does not reset counters on restart. A new allowance requires
an explicitly authorized new experiment, with historical artifacts retained.

## Acceptance

Inspect saved raw hashes, parsed bodies, shell pages, exact rule-source capture,
entity/period/metric diagnostics, original fetch failures and unfetched candidates.
Report actual provider requests, known tokens and missing usage separately.
Runtime success or a readable body is not semantic completeness. Evaluate source
quality blind to variant identity before changing production.
