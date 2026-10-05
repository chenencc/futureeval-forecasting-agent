# Frozen-frontier paired experiment V1

This is a development-material selection experiment, not fresh acquisition or a
forecast evaluation. Both arms start with the same five original questions,
33 complete captured responses, parsed documents, and original search leads.
The stored material is retrospective; opening timestamps are unavailable.

- Baseline: release-compatible `collection.md` paragraph-reading strategy on the
  shared runtime. This is the release's existing reading/banking path, not its
  production raw-recall path (which intentionally does not bank paragraphs).
- Candidate: `intelligent_materials_v1` material planning/frontier/assessment.
- Ultra is frozen for both arms. Super fallback is disabled; failures remain
  failed arms. No model selection is made based on quality.
- Twelve decisions, sixteen physical HTTP attempts, and 900 seconds per arm.
  No automatic second dispatch. Maximum ten arms / 160 model attempts.
- Zero Tavily, Exa, source fetch, Extract, supplement, analysis or submission
  requests. Only model HTTP calls use the existing authorized OpenRouter secret.
- Arm order alternates between cases. The case list, input hash, ordering, and
  fifteen exact material checks are committed before provider calls.

The immutable pool includes raw bytes but excludes prior plans, conversations,
selected excerpts, outcomes, and predictions. Its compressed SHA-256 is frozen in
`experiments/intelligent_frontier_rubric.json`. Search leads with no frozen body
remain unavailable; discovering another live body is outside this replay.

Primary metric: version-valid banked excerpts containing all predeclared anchors
from the specified original source. This is a conservative lexical proxy for
material selection, not exhaustive relevance or factual verification. Exact
coordinates and source versions are audited before scoring. All original bodies
are already present in both arms; captured-source recall cannot improve here.

Report per-case material checks, cost, unknown usage, failed arms, exact-span
integrity, wrong date/entity/metric selections on manual review, and explicit
gaps. Do not equate an agent's adequacy label with correct coverage. Do not treat
the candidate's extra assessment tool as a ground-truth advantage.

The experiment does not expose its rubric to either model. Machine reports retain
arm names for provenance. Any label-masked manual review must state that structural
output differences can reveal the arm; no strict blinding is claimed.

This branch repurposes the existing manually invoked `retrieval_trial.yaml` only
on `dev_acquisition_v2`; it changes no main production workflow or timer. Complete
state is uploaded per question. Cached completed comparisons return without new
requests, and interrupted sessions require review rather than implicit retries.

Frozen analysis dependency checks remain mandatory. Passing this development
replay cannot authorize production promotion; a separate untouched pilot is needed.

## Separately frozen Super replication

Ultra-only run `37287329839` produced zero terminal pairs: all ten arms were
interrupted by upstream overload. It consumed 42 physical attempts, with 88,572
known tokens and 32 attempts without reported usage. This is availability evidence,
not an acquisition-quality comparison. Original archives remain unchanged.

The next separately identified run freezes Super in BOTH arms with fallback off,
the same pool/rubric/order/context, and an equal lower eleven-HTTP-attempt ceiling.
It keeps twelve decision slots but cannot use more than eleven physical attempts;
forced closure starts before the physical limit. There is no automatic redispatch.
At most 110 additional attempts are possible; both runs together are bounded by
152, below the initial 160-attempt campaign ceiling. Compare strategies only
within the Super replication, never between an interrupted Ultra arm and Super.
