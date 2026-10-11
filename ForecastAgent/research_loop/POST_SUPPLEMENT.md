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
