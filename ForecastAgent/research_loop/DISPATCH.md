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
