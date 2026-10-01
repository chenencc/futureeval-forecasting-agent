# Tool delivery protocol

The acquisition runtime distinguishes four things: captured source material,
executed tool results, projected model input, and confirmed request inclusion.
None establishes comprehension, factual correctness, or forecast quality.

## Delivery and context

- Complete tool results remain in the durable ledger. Model views remove raw
  transport bytes and quarantined source text before any projection.
- The newest complete assistant/tool group has priority over older conversation
  and compressible task summaries. It cannot be silently evicted to fit the
  context ceiling. An impossible minimum context fails before model HTTP.
- Source reads retain exact text and coordinates. Large replies are delivered in
  slices with `next_start`; dataset reads retain complete rows with `next_offset`.
  The runtime shares a 6,000-character reading budget across a tool-call batch.
- Only after a successful model response are the exact projected slices recorded
  as delivered. Failed provider requests leave reads unconfirmed and available.
  Receipt hashes and model attempt indices support inspection of actual inputs.
- Generic bounded catalog replies explicitly disclose truncation. They do not
  create source-reading coverage. Useful material should be saved as exact quotes
  or excerpts before moving to another source.

Legacy execution-based reading claims are retained as `legacy_unconfirmed_reads`.
They cannot block new reading. This migration changes no search/provider budgets,
original requests, snapshots, excerpts, or prior transport records.

## Acquisition need lifecycle

The frozen plan is immutable. `set_acquisition_need_status` records an explicit,
reversible applicability decision and reason alongside the original requirement.
The operating clock is included in its audit record. At least one critical need
must remain active. Failure to find evidence is not evidence of inapplicability.

Inactive requirements remain visible in metrics and exported packages, but are
excluded from active collection gaps. This is an agent-declared scope decision;
the program does not certify its substantive correctness. Current-clock plan
reconciliation exposes potentially obsolete requirements without silently
rewriting them. Reactivation is audited by the same mechanism.

## Verification

Regression scenarios cover context pressure, exact partial delivery, complete
dataset rows, failed requests, invalid coordinates, temporal quarantine and
legacy restore. Real saved source replies can be replayed without provider calls.
Live acceptance still requires source relevance and semantic coverage inspection;
passing protocol tests alone does not justify a large collection campaign.
