# Material planning and read-retention repairs

This is an opt-in development policy, `intelligent_materials_v2`. Production
release 1.0.1, its analyst and its submission policy remain unchanged. The
[failed frozen-frontier V1 comparison](FRONTIER_RESULTS.md) remains archived;
its harness explicitly requests V1 even though the new pipeline default is V2.

## Three mechanisms

1. **Original-field handles.** At most four handles identify nonempty immutable
   original question fields. The model chooses `question_refs`; the program
   copies exact full fields and their coordinates/hashes into the plan only
   after validating the entire batch. Conditions still need precise entities,
   metrics, periods and exceptions. Whole-field binding is structural, not
   semantic verification. Legacy quote errors identify the offending need/span
   and any exact matching original field.
2. **Bounded planning repair and closure.** An initial failed plan has one
   correction opportunity. Two failed operations set a durable closure latch.
   The program exports gaps without another model request, including when no
   plan was frozen. Subsequent calls in the same returned batch cannot resume
   planning/acquisition. Failure history and all provider reservations persist;
   this never resets budgets or reopens a completed task.
3. **Exact review focus.** Saved readable spans remain in a dedicated context
   section until kept or explicitly rejected. The focus has at most two spans
   and 6,000 text characters; every original coordinate and source version is
   checked. Neutral inventories are compressed/evicted before this text. The
   normal context ceiling still applies, and original question fields/handle
   IDs are protected from truncation. The same first projected reading request
   already forces `review_passages`; another navigation turn is unnecessary.
   A response can batch review, assessment and closure without another turn.
   A forced program close always wins and exports unresolved span counts.

The focus is a saved-source refresh. A projection record alone is not proof of
successful request delivery, comprehension or relevance. Actual provider records
remain the audit authority. Keeping a span requires an explicit agent decision;
no automatic rule selects a target row or treats generic background as adequate.

## Zero-provider replay

The fixture `intelligent_repair_responses.json` preserves tool arguments from
Super run **37288399817**, with original provider-record and bundle SHA-256s.
The audit verifies these against the archived records before running regressions:

```sh
python -m ForecastAgent.acquisition.repair_replay \
  --source-root snapshots/intelligent-frontier-review-37288399817 \
  --output snapshots/intelligent-v1/materials-v2-offline-repair.json
python -m unittest ForecastAgent.tests.test_intelligent_repairs -v
```

- All five saved typed plans can be translated to field handles without changing
  their conditions. This is an explicit test adapter, not a new model decision.
- Replaying old invalid responses tests bounded closure: datacenter and gas
  sequences stop after two simulated responses, compared with 10 and 5 planning
  failures in the actual V1 traces. Those legacy responses use the old wire
  format, so this does not measure how a model would repair new V2 requests.
- The actual stablecoin table retains both USDT and USDC market-cap values after
  three neutral catalog groups, under an 18,000-character test ceiling, and banks
  their exact table text by passage ID. A separate mocked loop tests the first
  projected read's immediate review gate and same-response closure.
- Rejection, stale versions, atomic reference validation, long original fields,
  pending-gap export, preservation of reservations and migration rejection have
  regression coverage. The frozen 47 release dependencies remain hash-identical.

The structured English report is archived at
`E:/metaculus_data/reports/intelligent-materials-v2-offline-repair.json`.
The replay makes **zero model, search, source, analysis or submission requests**.

## Remaining gate

This validates control and source-retention mechanics. V2 autonomous selection,
adequacy assessment, real requests/tokens and frozen rubric recall remain
unmeasured. No promotion or broader recollection is justified yet. The next
provider experiment should use a separately frozen V2 identity with the same
five materials, model and physical request ceiling; do not reuse or mutate V1
tasks or reset their ledgers. Inspect assessments and actual downstream visibility
as well as excerpt counts. Historical current captures still permit leakage.
