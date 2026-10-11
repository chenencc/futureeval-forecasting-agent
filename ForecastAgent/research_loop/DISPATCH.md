# Bounded material and gap dispatch

Development opt-in: `research_dispatch_policy=material_gap_dispatch_v1`.
It requires the map-guided acquisition and source receipt policies. Production
and archived requests retain their existing policies and limits.

The frozen `research_dispatch_limits` object contains four integer allowances:
`map_updates=10`, `post_review_http=6`, `post_review_revisions=3`, and
`initial_http=16`. The outer experimental runner separately freezes 32 model
decisions, 40 physical model attempts, three service failures and 2400 seconds.
Tavily remains at three basic searches and Exa at one. No tool registration or
model change grants fresh allowance. Six decisions/attempts and 540 seconds are
reserved within these totals for final local review.

## Scheduling

1. Preserve mandatory planning and Exa obligations.
   With discovered URLs but no readable body, bind one capture batch to
   `read_sources` before empty-map inspection or optional skill navigation.
2. Read one pending exact source scope; the next phase updates that same source.
3. After a committed graph, offer one acquisition phase for declared obtainable
   consequential gaps. The agent chooses among available native network tools.
   Future realization requests do not force acquisition.
   Map action categories such as `page_fetch` to real offered functions. Require
   the declared gap link. A proposal rejected before a native reservation does
   not count as completed acquisition; allow one bounded correction.
   A bound capture menu offers at most 24 observed URLs and excludes failed or
   in-flight plain-fetch reservations. Lexical ordering is a navigation hint,
   not target relevance or a factual authority score.
4. Review each source scope at most twice. Failed or deferred interpretations
   remain pending and cannot starve other saved sources.
5. Final review rebuilds the pending-source packet after partial acceptance.
   Stop on complete processing, two nonprogressing accepted proposals, provider
   failure, reservation/deadline exhaustion or the graph revision ceiling.

Soft raw-recall stops do not discard an eligible bounded processing phase.
Hard model/time limits and forced closure still end execution. All selected
phases are persisted in `research_dispatch.selections`; this is scheduling
intent, not proof that an action succeeded. Real tool/provider journals remain
the evidence of execution.

## Acceptance

An exported package is not evidence completeness. `material_processing` reports
`processing_complete` or `processing_with_gaps` with exact pending material IDs.
Receipt closure certifies declared handling of delivered excerpts only. It does
not certify entire-page reading, target relevance, causal meaning or outcomes.
Direct-original and map-assisted scoring remain separate experiments.

## Discovery selection pool

The opt-in dispatcher gives the agent up to 24 observed, unread search leads
with titles, reported dates and short discovery excerpts. It does not truncate
the selection pool to the smaller number of pages permitted in one fetch call.
The agent selects a batch under the existing schema and remaining capture cap.
Saved pages, attempted canonical aliases and unobserved navigation links do not
become fresh capture slots. Question-specified sources are retained for initial
discovery selection; historical tasks retain archive routing.

Ordering and excerpts are navigation aids, not verified relevance or evidence.
The model must distinguish the target aggregate from related subgroups/issues,
and the current baseline from earlier terms or event stages. A larger eligible
pool does not prove correct selection, successful fetching or better forecasts.

For these two discovery phases, the native `read_sources` model schema uses
short `source_ids` instead of copied long URLs. The program validates IDs against
the exact turn registry, restores the unchanged native URL interface, and records
the ID-to-URL binding in the step journal before execution. Unknown IDs, duplicate
IDs and mixed ID/URL requests fail before a network reservation. No approximate
URL repair or inference is permitted. Other routes retain their native schema.
