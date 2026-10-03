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

## Source architecture

`evidence/source_identity.py` extracts observed Markdown/HTML URLs and keeps exact
transport identities. Query bytes, path case and substantive date parameters are
preserved. Slash variants are only possible aliases: selecting one for initial
capture defers the other with an explicit reason, never verifies equivalence.
Exact rule URLs remain eligible. Saved identical bodies form hash groups without
deleting any original capture or declaring the resources semantically equivalent.

`supplement/frontier.py` distinguishes seed sources from observed child links.
Page-link provenance survives copying into the source catalog. Child expansion is
limited to 4 per parent, 8 per host and 16 per task under the existing depth and
HTTP/render caps. Navigation routes, weak subject anchors and possible aliases
are deferred into the inspectable backlog. These bounds are collection policy,
not a completeness claim. Missing identifiers do not delete original bodies.

`evidence/source_coverage.py` exposes separate literal axes for identifiers, dates,
units, clock strings and measurements. A URL's date is separate from a date in
saved content. An observed data candidate requires compatible identifiers, body
dates and measurement structure. It does not select a resolved value, convert a
timezone, verify metric meaning or claim official authority. Prose keeps its
independent diagnostics; literal uncertainty is never an event non-occurrence.

`source_architecture_replay` validates these contracts using saved captures only:

```sh
python -m ForecastAgent.experiments.source_architecture_replay \
  --inputs saved-solid-collection-artifacts --output offline-review
```

It preserves cumulative provider journals and checks zero new reservations.
The resulting frontier is a prospective routing diagnostic, not evidence of
counterfactual live acquisition quality or actual HTTP savings.
# Observed material dependency recovery

Readable indexes and download instructions are retained as context. They do not
establish that the requested observations have been acquired. The agent receives
`material_requirements` with observed download/archive URLs and reported retention
windows. Missing target files remain explicit gaps.

The supplement normalizes string, dictionary, relative and Markdown links.
Verified saved HTML can recover labels and links omitted by an adapter; navigation
links remain excluded. It never guesses a `down.txt` URL, rewrites station IDs or
fabricates historical endpoints. Date, station, unit and clock checks remain
separate; neither route context nor a filename verifies a value or timezone.

Observed target-date files, issuer/period attachments and focused result indexes
receive priority using the parent body hash and recorded link provenance. Broad
feeds cannot lend their topic to unrelated results. The existing branch, depth
and shared HTTP/browser limits still apply. Cached readable parents expand their
dependencies without another fetch; resume never repeats spent reservations.

The regression suite uses mocked transports and preserved snapshots. Live
retrieval and rolling archive availability still require separate acceptance.
