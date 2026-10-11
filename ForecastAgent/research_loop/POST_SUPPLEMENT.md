# Reserved post-supplement map review

The `reserved_local_map_review_v1` policy is optional and development only.
Intelligence map-enabled inputs select it explicitly. Existing sealed inputs and
production workers keep their pinned policies and code identity.

## Flow

1. Freeze the input, code, model routing and cumulative budget.
2. Reserve two existing model decisions and HTTP slots, 180 seconds of wall time,
   and the final one of three map revisions before acquisition.
3. Run the original acquisition Agent with the remaining allowance.
4. Preserve its original bundle and independent supplement archive.
5. Build a local reading packet from pending saved sources. New sources get priority;
   each receives up to two exact spans, within 16 handles and 24,000 characters.
   Each source gets a first opportunity before a second span is selected. The
   strongest substantive span precedes its header; ranking is a navigation
   heuristic, not proof of relevance. Original coordinates and text stay exact.
   A long document cannot occupy the whole packet. Unexposed sources remain recorded.
6. Ask the configured acquisition model for one map update with per-source receipts.
   One rejected proposal may consume the remaining reserved request. Provider retries
   also consume the physical HTTP cap. Never repair facts to manufacture acceptance.
7. Export a derivative graph and its delta, accepted/rejected receipts, usage and
   remaining gaps. Keep original task result, captures, searches and quotas intact.

The model must separate target observations from background, different entities,
currencies, geographic basins, participants, periods and future events. Exact quotes
and source receipts are mechanically checked; applicability and causal meaning are
still unverified judgments. Two excerpts do not establish complete page reading.

The packet exposes separate node, gap, material and inspected-reference registries.
Receipt `gap_ids` copy only current program-generated G IDs; use an empty list
when none applies. New requests receive IDs after acceptance. Receipt incorporation
still requires a retained source-bound observation; rejected nodes cannot support
it. Length guidance matches the existing validator, including 300 characters for
`revision_reason` and 180 for literal `stage_basis`. No text is silently truncated
to make it pass. Earlier observations may inform a future forecast as background;
a future realization is not an obtainable missing document.

For an observation with an overlong stage annotation, the stage field is isolated
to unknown and its basis is cleared, with the complete rejected annotation retained
in the audit. The original claim and its references must still pass strict binding
checks; false quotes are rejected. This extends the existing unsupported-stage
isolation instead of discarding an otherwise valid observation. It does not
truncate, rewrite or certify a fact.

When a saved source contains a numeric Markdown table, the reading packet prefers
a literal data row over a title or separator and includes the header of the same
contiguous table. Missing-cell penalties are navigation hints only. Dates, values,
units and table contents are never synthesized. Other rows and unread sources
remain in the immutable snapshot; two handles are still not complete table reading.
Prior-map prompt projection contains the same node claims and reference IDs,
without duplicate binding bodies or the obsolete material hash. The reading
packet supplies the one authoritative current material hash. This changes prompt
delivery only; complete prior bindings and graph journals remain in the snapshot.

## Failure and restart

The review derives its pending latch from validated current-scope receipts before
reading and before saving. A zero-pending input performs no model call. Historical
graph hashes may differ after valid irrelevant/duplicate acknowledgments; the
graph and receipt journals stay unchanged. Current processing status is distinct
from target evidence adequacy and truth. Saved outputs remain frozen: replay a
changed implementation in an explicit derivative directory, not over its parent.

No reserved allowance, expired wall time, exhausted map revisions, malformed proposals,
or provider failures produce an explicit status and preserved originals. A completed
review is checksum-checked and cached. A crash with an existing HTTP receipt requires
review instead of silently issuing another request. `model-http` reservations persist
before transport; failed attempts and unknown usage remain in the report.

Saved-material experiments use a separate frozen derivative directory and explicitly
declared verification allowance. They do not reopen prior collection tasks or reset
their native budgets. They test the reading/update interface, not fresh acquisition,
causal truth or forecast quality. Paired scoring inputs must retain identical original
coverage; forecasts and submissions require separate execution gates.

## Bounded context and explicit processing feedback

The local review packet preserves its original fair body/row allocation and adds
at most twelve literal section/table-header context references within 6,000
characters. Companions come from the same saved source and keep exact coordinates.
They are navigation hints, not proof of date, metric, authority or settlement
status. Selecting a quantitative row without its available local context produces
an advisory retry item; no node is rejected or automatically given context.

Model-facing material descriptors retain exact source/body/scope identity and
inspected IDs, plus a count of accessible references. The complete undisplayed
reference directory stays local. Removing its repeated ID list does not reduce
retained originals or grant reading credit for undisplayed text.

Retry feedback separates actual graph additions/changes from accepted source
reviews. It identifies unacknowledged delivered material and source-bound nodes,
so the next bounded reply can supply justified material_reviews or defer unfinished
scope. It never creates receipts automatically. The complete response/acceptance
journal remains on disk; the next model request receives compact actionable
feedback instead of another copy of the full previous outcome. Existing HTTP,
failure, revision and no-processing-progress limits still apply. Offline packet
size reductions are not actual token savings or evidence of better forecasts.

## Model context metadata projection

When an integrated acquisition request exceeds its delivery ceiling, compact
navigable source summaries and mirrored feedback before paging original spans.
Immutable rules, current nodes, targets, gap requests, cursor, budget, operating
clock, rejection meanings, material identity, partial scope, and processing
handles remain. Audit-only hashes and repeated descriptions stay in the durable
ledger. Corrective map feedback remains available.

Whole original spans can then be paged, with delivered and omitted IDs recorded
explicitly. Quotes are never shortened. Omission does not establish absence,
full-page reading, target relevance, or an accepted processing receipt. If the
remaining immutable content cannot fit, fail locally before a model request.

Invalid enum values remain rejected. Their structured contract feedback includes
the failing field and allowed values, so a later model update can repair its own
proposal. This diagnostic does not change the accepted node, source quotation,
material receipt, target effect, revision, or budget. Plain semantic failures,
such as using another source's node for incorporation, remain explicit rejections.

## Source-specific review choices

Material descriptors expose up to three `same_body_material_ids` whose retained
body hashes match exactly. This is a byte-identity hint, not equivalence of URLs,
reading scopes, authority, or target relevance. A duplicate disposition still
requires this material's inspected reference, a related material ID, and the
model's reason. No receipt or graph node is generated automatically.

Rejected source reviews include a bounded repair context: exact material ID,
inspected reference IDs, source-bound observation nodes, and identical-body
material IDs. Cross-source incorporation remains rejected. A missing suitable
node requires a justified source-bound observation or an explicit unfinished
disposition. Successful processing does not prove the target fact or forecast.

## Scoring target metadata

Bounded scoring keeps each target ID, kind, condition, expected source, status,
linked-node IDs, obtainable-request status and adequacy warning. A condition that
exactly equals the immutable question uses an explicit `immutable_question`
reference; other conditions remain complete. Link roles, effects and reasons stay
on the delivered nodes instead of appearing a second time in target summaries.
The full delivered-subset target audit is retained in the scoring audit file.

Node packing and the full request byte limit still apply. Omitted nodes remain
explicit; a map with no delivered observation falls back to original evidence.
This projection improves delivery space, not factual validity or probabilities.

## Saved-material consistency review

`post_supplement.run(..., consistency_review=True)` can revisit processed saved
materials in a separately registered derivative review. It does not reopen native
collection tasks, reset historical budgets, capture more pages, or certify source
meaning. Explicit HTTP and failure caps still apply; code/input/model drift is
rejected on re-entry. Default review behavior remains pending-material processing.

The bounded reading packet offers recent literal dated JSON rows and existing
node references, then other excerpts. Exact body coordinates remain authoritative.
`existing_node_reading_scope` exposes omitted references. A source processing receipt
is distinct from complete reading, correct interpretation and target adequacy.

Advisory link-review cards show each node's own reference IDs alongside the exact
frozen target kind/condition and its proposed role/effect/reason. Cards do not add
cross-node factual support. Their limits are32cards and6000characters; complete
conditions are kept or omitted explicitly, never silently truncated. These cards
currently enter the saved-material review request, not every acquisition request.

Review the interpretation and every link reason together: do not promote publication
time to event time, broaden issuer/geographic scope, or label a historical baseline
as direct evidence of an unknown future realization. When combining observations,
retain the inference as an explicit hypothesis. Programs bind originals and report
scope; they do not infer semantic truth or automatically retire valid observations.

## Experimental original-first review

`source_first=True` adds an independent original-only reading before a graph update.
The first request whitelists the immutable question, clock and delivered original
spans/context. It excludes old graph nodes, interpretations, links, processing
receipts and node-review cards. The model selects source-local reference IDs and
provisional date/entity roles. Unknown or cross-source IDs are isolated; valid
siblings remain available. Binding verifies original identity, not semantic truth.

Both calls share the same existing model HTTP/failure journal and deadline. No
additional allowance is granted. With only one request available the stage can
finish original reading without updating the map. Its status is
`source_reading_complete`, not a completed graph review. Invalid or failed initial
reading is preserved and does not silently dispatch another call. Interrupted
receipts retain the existing review-required recovery rule.

The second request receives a compact provisional reading with the same original
R IDs. Full bound text remains in the local audit; it is not duplicated in the
second request. Both old and independent interpretations remain unverified and
must be reconciled against their originals. This option is experimental and off by
default; offline tests do not establish better semantic or forecasting performance.
