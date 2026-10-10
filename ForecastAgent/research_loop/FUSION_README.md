# Map-guided acquisition experiment

The optional [critical-gap feedback extension](GAP_FEEDBACK_README.md) records
declared decision impact, native action links, source-version dispositions and
the actual graph delta. Existing experiments keep their previous policy.

One Super agent decides both what to collect and how to update a provisional map.
Search, reading, source repair and provider accounting use the existing native
tools. There is no nested model call inside map or acquisition tools.

## Decision and dispatch accounting

`COLLECTION_MAX_TURNS` limits received model replies, including replies with
invalid or absent tool calls. Program-only source recovery does not consume a
model decision. Transport retries and credential/model fallback attempts still
consume their actual physical HTTP reservations; failure and lifetime caps remain
independent. A received reply consumes a decision before delivery processing.

Each session records `model_decisions`, `scheduler_iterations`, `program_steps`,
`program_step_limit` and `decision_count_policy=received_model_replies_v1`.
The separate local-step guard is `max(8, decision_limit)`; it and the deadline
bound program-only recovery. `turns[].turn` is the model decision ordinal;
`turns[].scheduler_iteration` also includes preceding program-only steps.
`program_dispatch_limit` remains compatible with existing resume handlers;
`exhausted_budget` distinguishes `model_decisions` from `program_steps`.
Previous sessions and physical reservations remain unchanged when resuming.

Final review keeps its configured reservation. In the 32-decision/40-HTTP trial,
reserving six for final review leaves 26 received decisions and 34 physical HTTP
attempts for native collection. One program-only recovery plus 26 model replies
therefore requires 27 scheduler iterations. Early completion, stalls and hard
limits can still stop sooner; remaining allowance does not require empty calls.

## Feedback loop

1. Freeze the question, exact rules and material plan.
2. Export stable target IDs from the plan; they are unverified research conditions.
3. Link a source action to target or current map node IDs using
   `research_node_ids` on ordinary discovery/reading tools.
4. Reserve the action intent before native quota reservation. Preserve outcome,
   changed body hashes, new leads, map revision and remaining budget.
5. Inspect exact saved-body references and update observations, hypotheses,
   unknowns and relations after a useful batch. At most three accepted updates.
6. Use map material requests to choose the next explicit native tool action.
7. Stop with recorded gaps at the shared budget or deadline. A complete map is
   never a prerequisite for closure or a valid direct forecast.

The source-first node contract checks literal quotations and coordinates. It does
not certify truth, target relevance, causal meaning, source independence, or unit
interpretation. An inaccessible page or unpublished future outcome is never NO.
Source texts, search hits, ledgers and prior map revisions remain separately saved.

## Opt-in policy

```json
{
  "research_state_policy": "incremental_research_v1",
  "research_acquisition_policy": "map_guided_acquisition_v1"
}
```

Use the actual `POLICY` exported by `ForecastAgent.research_loop`; the program
validates it. Policies are experimental, local and disabled in production.

## Paired pilot

`fusion_trial` runs one to five explicit questions with newly created acquisition
ledgers. Both arms use the same acquisition entry point, Super model, tools,
question/rules, limits and deterministic supplement stage. The baseline disables
research-map feedback. This isolates the feedback mechanism; it is not a claim
that an entire production release was compared to a new release.

Per arm: twelve decision turns, sixteen physical model attempts including failures,
three Tavily basic attempts, one Exa attempt, eight initial source reservations,
one basic Extract batch, and the same two-browser/two-HTTP supplement caps. No
old task budget is reset. Resume uses the remaining arm allowance. Input hashes,
implementation hashes, provider receipts and original capture timestamps are
preserved. Mercury uses the same target-only scoring code and registry in both
arms. Map notes are optional; original source evidence remains available.

Both arms explicitly select the raw `intelligent_materials_v3` entry point.
In the fusion arm, suggested source actions are advisory; plan prerequisites,
mandatory Exa discovery and hard closure remain enforced. A map revision bound to
a distinct readable body-hash inventory counts as planning progress for the stall guard,
never as new readable material or relevance credit. Repeated updates to the same
body cannot clear that guard. Map updates still share the ordinary model turns.
After the first readable body batch, initialization reserves at most four ordinary
model decisions: inspect relevant exact handles, then propose the first map.
Failures consume those decisions. There is no nested model, mandatory perfect map,
automatic search or override of hard closure. Further updates are agent choices.
The allowance includes reinspection or format correction. Literal quote and event
time contracts are repeated beside their schema fields. The program never repairs
claims by inventing evidence. Raw export closes locally and needs no model-summary
reserve; physical transport, failure and deadline limits remain authoritative.
Map proposals use a 6500-token output allowance with a requested 1200-token hidden
reasoning cap. Other actions retain their original allocation. The cap is a
provider request, not a promise; inspect actual reasoning usage and finish_reason.
An empty/truncated assistant reply cannot evict the newest completed source tool
group. A compact binding frame (revision, current material hash, saved-source count
and inspected handles) survives state compression. During a pending map update,
at most four exact previously inspected spans are rehydrated across dispatches.
Their coordinates and body hashes are checked again; context space is reserved
before optional inventory compression. This is local delivery, not new source
acquisition or a budget reset. The current revision/hash are
also enums in the map tool schema. Stale body handles remain rejected; navigation
metadata alone never certifies source interpretation or factual adequacy.

Active schemas expose the current material-need IDs and research-node IDs as
separate enums. Network actions normalize only a known need-ID scalar to its
singleton array and remove duplicate known set members. Normalizations are
recorded beside the action intent. Unknown IDs, semantic fields and provider
limits are not repaired. Research-window dates belong in discovery queries;
target dates in conditions still require immutable question/metadata lineage.

Saved reading accepts an exact transport URL or one unambiguous canonical alias.
This supports supplementary URLs with a trailing slash without changing the
original body, coordinates or capture time. An ambiguous alias is rejected.
Search/catalog inventories are compressed against the global remaining context
space, after reserving immutable rules and exact inspected spans. They do not
receive an independent allowance that can overflow the total ceiling. Exact
reader payloads stay protected; an irreducible reading batch still fails closed.

## Saved coverage and stage support

The optional `research_grounding_policy: saved_coverage_stage_v1` strengthens the
map-guided interface without adding a model, search or fetch inside a tool.
It is disabled when absent and requires map-guided acquisition. Existing frozen
experiments and production retain their previous policy.

`inspect_research_state` adds a paginated source catalog with body hashes, saved
and accessible character counts, truncation flags, lexical date extents and span
counts. These describe saved navigation, not relevance, event timing, reading
credit or factual completeness. Restricted visible windows remain restricted.
Local URL/query and paired ISO date filters return original reference handles.
Omitted matching spans remain counted; a short reading is not missing evidence.
Source opening text, nearby section/date headings and the nearest supported table
header are delivered as exact catalog spans, with hashes and coordinates, and
pinned across dispatches. Heading navigation does not cross a hidden gap between
restricted visible windows. Old or
changed-body handles cannot be rehydrated. No additional raw text is acquired.

The new map schema requests `stage_basis`, a short literal quotation in the
node's bound references, for planned/ongoing/completed observation labels. A
missing or invented basis quarantines the stage label to `unknown`, preserving
the original observation and recording the proposed label in the acceptance
audit. A literal basis validates provenance only. A date, a `Close` column, or a
source saying `Market Open` does not automatically certify completion. The agent
must reconcile context and limitations; semantic errors can still pass a literal
binding check. Archived proposals without this optional field remain readable.

Unsupported `source_stated` time annotations are also isolated to an empty
`event_time` with `time_status=unknown`. The literal observation, proposed label
and rejection reason remain auditable. A bound date can still describe publication
or a different period; this check never establishes its event role. Invented
quotes, stale handles and malformed nodes remain rejected. Acceptance events
record the label policy version; a prior cache cannot silently certify a new
policy. The opt-in tool schema lists exact inspected/context handles, explicit
reading limits and empty references for unknown nodes. It does not repair model
semantics or increase tool/provider caps.

Delivery omits plaintext reasoning and duplicate `reasoning.text` details while
preserving opaque continuity records, complete tool-call arguments and disk
originals. This prevents a large failed map from being stranded behind its own
duplicated reasoning. Exact evidence is not shortened to solve this failure.

The frozen three-case pilot on `da35df9` covered public health, AI release and
sports questions, including discrete, binary and multiple-choice targets. Each
arm had four physical Super attempts, four decisions and 420 seconds. Both arms
saved one map out of three; baseline used 9 requests / 76,801 reported tokens,
and coverage-aware used 9 / 89,636. All 18 requests returned. No acquisition,
Mercury, forecast or submission ran. Live improvement was not demonstrated.
The subsequent offline replay fixed the failed-context delivery and preserved
five literal WNV observations with unsupported timing isolated. It did not repair
target-applicability mistakes, invented handles or an invalid gap citation array.
These offline fixes are not a new model result or a forecast accuracy measure.

Validate the interface on identical saved bodies before fresh acquisition. Treat
source coverage, delivered original text, model requests, accepted bindings and
semantic findings as distinct measures. No new forecast quality claim follows
from a successful literal binding or a larger archive.

The execution order alternates between questions. Live search and page responses
may differ; observed coverage changes are exploratory, not a controlled recall
estimate. Inspect whether source actions occur **after** a map update, whether
they are linked, and whether they acquire relevant new material. Links alone are
not proof of intelligent discovery. Text volume is not factual adequacy.

```text
python -m ForecastAgent.research_loop.fusion_trial \
  --parents FROZEN_PARENT_LIST --root NEW_EXPERIMENT_ROOT
```

Preparation sends no provider requests. Use `--execute --limit-cases 1` to review
the first paired smoke case before executing all five frozen cases. Repeat without
the limit to continue; completed pipeline and decision caches consume no new calls.
Execute only with authorized local
credentials loaded into process environment and the model fixed to Super. Add
`--execute` for the bounded live experiment. Never print credentials. The pilot
does not submit predictions or modify the production worker.

## Native full-loop acquisition trial

`full_loop_trial` delegates collection to the complete native acquisition runtime.
It never stops at the first accepted map and never calls a scoring model. Fresh
question-only inputs exclude old bodies, forecasts and resolutions. The opt-in
coverage/stage policy is enabled explicitly. A trial has 12 model decisions,
16 physical model attempts, two model failures, 900 collection seconds, three
Tavily basic searches, one Exa search, eight initial capture reservations and
three map revisions per case. The separate release supplement keeps its existing
two HTTP and two browser operation caps. Browser subrequests are not initial
capture reservations, and supplement bodies are not credited as agent feedback.

```text
python -m ForecastAgent.research_loop.full_loop_trial \
  --parents FROZEN_PARENT_LIST --root NEW_TRIAL_ROOT --execute --limit-cases 1
```

Review the first case, then omit the limit to process the remaining frozen inputs.
Completed trial results are read without new provider calls. Interrupted results
remain preserved; this harness does not silently grant another dispatch. The code,
question fields, original snapshot checksums, budgets and first-start clocks are
frozen. Changing them requires a separately identified experiment.

The feedback audit links a source action to a **later** map revision only when an
observation binds the exact newly captured body checksum. It separately records
actions after a map, linked intents, new readable bodies, actual later bindings,
unreviewed supplementary changes and explicit native stop reasons. A link, a
checksum, or a successful export cannot certify target relevance. Compare useful
dated baselines and leading indicators manually, preserving unpublished future
results as gaps. Earlier saved-body map tests are interface references; they are
not full-loop controls or a controlled recall/accuracy comparison.

The first native smoke case exposed two invalid `read_sources` parameter shapes.
It stopped before any initial fetch or map; two independent supplement captures
must not be presented as agent feedback. Raw map-guided capture now exposes only
URL capture parameters, without unused paragraph-query metadata. The trial also
enables the existing one-time observed-source recovery; reservations remain shared.

A new readable body batch can drain through a bounded local inspect/update cycle
before soft no-progress, discovery-frontier or source-capture exhaustion stops.
The cycle allows at most four local tool executions for a material/revision pair,
with at most three accepted revisions. Repeated invalid calls still reach the
existing error stop. Physical model, decision and deadline caps remain unchanged;
local work never receives raw-recall progress credit. This applies to initial and
later map updates. The policy is opt-in and production collection is unchanged.

A subsequent smoke case returned three length-limited responses with no usable
tool call: hidden reasoning consumed most or all of each 3000-token output cap.
All map-guided tool requests now specify a 1200-token reasoning budget; ordinary
requests retain 3000 total output tokens and map updates retain 6500. This is a
provider request setting, not proof that a backend strictly honors the limit.
Response finish reasons, actual reasoning usage and missing tool calls remain
auditable. Physical request caps and failed-run records are preserved.

When source catalog metadata would overflow the global context allowance, its
duplicated binding-frame directory is omitted with an explicit flag. Exact
inspected quotations and heading/table context remain pinned, and the immutable
objective is retained. The full directory remains available through local
inspection. If those protected materials alone do not fit, projection still
fails without a model request; no source text is silently shortened.

Use `--continue-from PRIOR_TRIAL_ROOT` only to migrate confirmed interrupted
collection state into a distinct experiment after a code repair. The migration
freezes the old directory checksums, copies the exact provider receipts and
ledgers, preserves the original first-start clock, and uses only remaining
physical, decision and search allowances. It rejects changed question inputs,
model/budgets, unknown reserved model receipts and already closed tasks. The old
trial stays immutable. A failed or unconfirmed copy is preserved for inspection.

An inspection reply may refer to exact text already pinned in the binding frame
instead of sending a duplicate directory and quote copy. This projection requires
the same material hash and complete equality of every delivered span, including
text and coordinates. Partial, stale or unpinned readings are left unchanged.
Revision, current map navigation and pagination remain in the tool reply. Full
original replies and bodies remain on disk. This reduces delivery overhead
without raising the 28000-character context ceiling.

## Preserved snapshot feedback repairs

Three separate opt-in policies implement faithful reading, graph patching and
bounded delivery: `preserved_views_v1`, `explicit_delta_v1`, and
`bounded_reading_delta_v1`. They do not alter the default production worker.

Recognized forum JSON becomes a deterministic reading view with original body
hash, decoded-view hash, JSON post paths and separate timestamp metadata. It is
not described as a raw-byte quotation. Original JSON, HTML and provider records
stay unchanged. Inline image encoding is omitted from textual span navigation
with original coordinates retained; the image is not counted as read. Restricted
original coverage never enables a whole-document decoded view.

Map `update_mode=merge` supplies additions/replacements, preserves omitted nodes,
and deletes only explicitly retired existing IDs. Rejected replacements retain
an older node only when its existing evidence still validates. Rejections remain
in the audit. `replace` keeps strict explicit-retirement semantics with actionable
missing IDs. All updates retain the lifetime revision cap and exact-text checks.

The bounded delivery policy sends immutable rules once, an exact selected reading
frame, current nodes, actual available tools and the latest complete tool group.
Historical messages and full source directories remain durable navigation data.
It retains the 28,000-character ceiling and fails explicitly if the irreducible
objective/reading window is still too large. The native runtime passes its actual
forced tool to context projection, avoiding contradictory next-action guidance.

`snapshot_loop_trial` permits only native local inspection and map update tools.
Each case has at most four additional physical model attempts in a distinct
verification ledger. Historical collection/provider allowances are preserved.
Already closed acquisition tasks are not resumed. Unbuilt maps use two staged
batches from the same saved snapshot to test initial construction and a later
update. No source or search network request, scoring or submission is enabled.
A passed saved-material feedback trial is not live retrieval or prediction
accuracy acceptance. Freeze its code and original hashes before execution.

Rejected updates now return their node IDs and precise errors in the bounded
context, with prior valid nodes retained. An unchanged accepted graph does not
consume a map revision or acknowledge new material. Material-hash changes and
rejection-generated prose are not graph progress. Merging also preserves old
material requests; quarantine occurs before strict final aggregate caps.
After a rejected patch, the native scheduler requires another local inspection.
Inspection readiness is bound to the material and current graph revision, and
does not survive an unsuccessful patch. Source metadata pagination cannot choose
a reading URL. Default saved-body reading separates headings from body spans,
retaining headings as context and preserving short table rows and exact text.

The first saved-snapshot repair pilot used 12 Super HTTP attempts and 77,534
reported tokens. The AI case bound a newly promoted saved forum source in its
second map; WNV and ALCS did not add a new bound observation. Offline replay of
their actual rejected proposals demonstrated unchanged old graphs, no new
material acknowledgement, complete rejection feedback and a return to local
inspection. Original source records and historical collection allowances were
unchanged. The subsequent two-case validation has a separate four-attempt
verification ledger per case; it does not renew the original collection quota.

The final native envelope also isolates an invalid child label without discarding
valid sibling observations. The presentation schema still shows correct limits;
the test runner uses the same native validator as collection. Default local reads
skip media-only lines, standalone clock labels and bare website names, preserving
their originals and all table rows. Newly delivered spans remain in the binding
frame after a partial map. Reading readiness is recorded even when there was no
new physical acquisition in that call.

An update over unchanged saved material is classified as an interpretation
correction in its audited delta metadata. No model claim, date, quote, node ID or
source reference is rewritten. Duplicate old observations and gap-only additions
cannot acknowledge pending new saved material. The audit retains that pending
review even when the physical source hash equals the current map hash.

Verification on 2026-10-09 used 21 additional Super HTTP attempts, all returned,
with 142,302 reported tokens and no additional searches or source HTTP captures.
148 focused and adjacent tests passed. Under exact-output native replay, WNV and
AI incorporated an observation bound to later saved material. AI also passed the
first live saved-material trial. WNV's original live trial was blocked by the
test runner's whole-batch child-schema check; replay isolates the long invalid
label and retains the valid 16-country observation without changing model output.
The ALCS model continued to reuse prior source notes. Its final exact-output
replay records unknowns and retires a duplicate, but has no newly bound source
observation. It remains a gap-bearing case and is not counted as feedback success.
No further model requests were made after the bounded final one-request probe.

This is two of three saved-material incorporation checks, including replayed
acceptance, not three live passes. Literal bindings do not establish predictive
quality: September WNV data is a baseline for the later October target, forum post
timestamps do not establish a model's release date, and an ALCS schedule does not
establish the score after Game 4. The production worker is unchanged.
