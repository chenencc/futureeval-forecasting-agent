# Critical-gap feedback experiment

One acquisition agent plans the next information action and updates its small,
fallible map. Final Mercury scoring is unchanged. This extension adds no model
inside a tool and does not introduce probability propagation.

## Enable explicitly

```json
{
  "research_state_policy": "incremental_research_v1",
  "research_acquisition_policy": "map_guided_acquisition_v1",
  "research_update_policy": "explicit_delta_v1",
  "research_gap_policy": "critical_gap_feedback_v1"
}
```

Saved-coverage grounding, preserved reading views and bounded delivery are
recommended. The policy is local and experimental; production remains unchanged.
Old experiments retain their old contracts when this flag is absent.

Both model prose and a program-generated explanation are preserved. The latter
lists only applied node/relation/request edits, accepted material dispositions and
rejected proposals. A model saying it added a node does not make that node accepted.
Receipts can cite up to 16 inspected handles, matching retained reading handles;
every handle must still belong to that exact saved source version.

## Gap-driven actions

Each material request states:

- `decision_impact`: what finding could change the target interpretation;
- `importance`: high, medium or low;
- `availability`: available, uncertain or a future event;
- the existing target, reason, node IDs, source role and suggested native tool.

The program derives stable `G...` identifiers from the target, role and node IDs.
Native tools accept `research_gap_ids`. The existing action journal saves those
IDs and an immutable snapshot of their declared impacts before reserving provider
quota. Duplicate actions cannot run again by changing their gap tags. Before a
map exists, frozen plan targets remain available through `research_node_ids`.
Gap links are optional to preserve mandatory plan/channel operations; an unlinked
action remains visible as such and receives no inferred gap relevance.

The queue favors obtainable, consequential gaps with fewer previous attempts.
This is a deterministic heuristic over fallible agent labels, not a calibrated
value-of-information calculation. Future-event uncertainty is never event absence.

## New-material receipts

`inspect_research_state` supplies `M...` material IDs beside exact reference
handles. IDs bind a URL, raw body hash and visible reading scope. Changed bytes or
scope invalidate earlier receipts. `material_offset` pages pending receipt
metadata; reading still requires a URL/query/offset. At most four exact inspected
spans are pinned in context; up to sixteen already-delivered handles survive
across local readings. These do not grant new network or evidence credit.
The span-index pagination cursor, its selectors, delivered count and exhaustion
state survive context projection. Character/byte coordinates are not pagination
indices. The snapshot runner uses the native bounded read-then-update schedule
and reserves map delivery within its physical request cap.

Every update includes `material_reviews`, with one of five dispositions:

| Disposition | Mechanical requirement |
| --- | --- |
| incorporated | An inspected reference and a retained observation bound to that source |
| conflict | Both observations remain, with at least two distinct source body hashes |
| duplicate | An inspected reference and an explicit related saved material ID |
| irrelevant | An inspected original reference and a concrete reason |
| deferred | A concrete reason; the material stays pending |

Reviews also name affected nodes/gaps and a declared effect: supports, opposes,
narrows, no change or unknown. Unincorporated material cannot claim directional
support. Bad siblings are isolated. An invented receipt cannot acknowledge a
source merely because another observation was accepted. Blocked/temporally
excluded pages remain outside the readable receipt inventory and retain their
original capture failure records.

These checks certify identifiers, literal bindings and declared treatment only.
They do **not** certify semantic relevance, an entire page being read, causal
truth, independence, source reliability or a gap being resolved. Source provenance
separates native action captures from saved input/external-stage material.

## Explain every change

`revision_reason` must explain what changed or why no graph change was warranted.
The program separately computes added/changed/retired nodes, relation changes and
material-request changes. The receipt journal stores the model explanation,
actual graph delta, source version, action provenance and map-event identity in a
checksum chain. Explanations and declared effects remain unverified.

A valid irrelevant/duplicate review can acknowledge material without adding a
node or consuming a graph revision. Deferred, unreviewed and rejected materials
stay pending. Review-only replies do not count as graph/recall progress. Exact
repeated reviews cannot fill the journal. At most twelve receipt events are
allowed; map updates remain capped at three, and the bounded local-cycle, model,
search, fetch and deadline limits are unchanged. Forced closure retains gaps.

## Validation

```shell
python -m unittest ForecastAgent.tests.test_research_gap_feedback -q
python -m ForecastAgent.research_loop.snapshot_loop_trial --sources original-bundle.json --root new-gap-trial --gap-feedback
python -m ForecastAgent.research_loop.snapshot_loop_trial --sources original-bundle.json --root new-gap-trial --gap-feedback --execute
```

The snapshot runner freezes input bytes, code hashes, model and a maximum of four
physical Super attempts per case. It executes local reading/map tools only. All
historical provider ledgers and page records remain identical. Receipt generation
and selected new source incorporation are reported separately from pending
material counts. No search, source capture, Mercury scoring or submission runs.
Saved-material success does not establish fresh acquisition recall or forecasting
quality; prospective, paired validation is still required.

### 2026-10-09 bounded validation

- 238 related research tests passed, including gap/action quota preservation,
  all five source dispositions, isolated rejection, append-only recovery,
  cursor delivery and explanations of rejected node proposals.
- Three saved public-question bundles received 12 Super reading requests, then
  one bounded finalization request each after restoring the native scheduler and
  pagination cursor. Total: 15 HTTP attempts, 146,303 known tokens, zero reported
  model cost; no searches, source requests, Mercury calls or forecasts.
- None passed the strict new-source-incorporation loop gate. Actual changes and
  rejection reasons were preserved instead of being reported as success.
- Exact-response offline replay preserved three deferred ALCS source receipts;
  WNV quote-format mismatch, OpenAI rejected-node links, and ALCS source-version
  mismatch remained explicit. Offline replay is not another model execution.
- The final receipt reference limit matches retained reading handles. A program
  explanation distinguishes applied changes from the model's proposed changes.

The bookkeeping mechanism is implemented. Reliable live source-to-map closure
still needs a small follow-up validation. Conditional probability experiments
were not expanded, and production was not changed.

### Saved binding closure follow-up

The opt-in policy now restores display-only quotation differences to an exact
slice of inspected saved text. It folds whitespace and ordinary HTTP Markdown
link display, with reversible original/view coordinates. It does not change
numbers, dates, negation, units, names, punctuation, translations or paraphrases.
Ambiguous matches, unread/stale references, cross-span stitching, and restored
quotations exceeding native field limits remain rejected. An explicit URL in a
submitted quotation cannot be replaced by a different URL.

Submitted replies remain intact. Accepted changes carry the submitted proposal
hash and format-binding proofs: source URL/body hash, reference ID, literal value,
coordinates, parser/view identity and JSON provenance when applicable. Native
literal binding and stage/time isolation still run after restoration. These proofs
establish source provenance, never semantic truth or target-period applicability.

Material metadata supplies `inspected_reference_ids` and
`bound_observation_node_ids` for the exact source version. A node bound to another
source cannot acknowledge the material. Receipt reasons prefer 240 characters;
audit storage accepts up to 800, while context projects counts and actual edits.
An oversized explanation does not silently lose text or bypass storage limits.

Validation reused the same three originals and saved model replies:

| Focus | Exact saved-reply comparison | Final receipt treatment |
| --- | --- | --- |
| WNV | Restored line wrapping; retained new node n3 | incorporated |
| OpenAI | Restored Markdown link; retained N1 through N4 | incorporated |
| ALCS | Original cross-source incorporation remains rejected | one corrected response recorded as deferred |

The ALCS correction used one Super HTTP request over saved text only (21,293 known
tokens, zero reported cost). Its live response corrected the source mismatch but
exceeded the old reason limit; exact offline replay accepted it under the final
storage contract. No second correction request was sent. The two incorporation
results are offline replay, not fresh collection runs. All original pages and
provider budgets were preserved. 251 related tests passed.

The three focused receipt checks pass; complete acquisition is not claimed.
WNV still lacks the future target publication. OpenAI's paired input covers one
forum source; two upstream held pages remain outside that comparison. ALCS still
lacks the future Game 4 outcome. Mercury, production and conditional-probability
experiments were unchanged.
