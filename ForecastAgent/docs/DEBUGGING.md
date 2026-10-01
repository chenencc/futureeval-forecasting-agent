# Single-case collection debugging

Use saved bytes to reproduce a failure before spending another provider call.
The `runtime_collection_five.yaml` workflow accepts an optional `question_id`.
When set, it restores the existing campaign and runs only that task through
`ForecastAgent.debug_trial`. The remaining four task ledgers must remain
byte-identical. A closed task cannot be reopened implicitly.

The command keeps the question, cutoff, quotas, previous Tavily/Exa attempts and
model transport prefix unchanged. `debug_before.json` and
`debug_dispatch_summary.json` record the baseline and incremental expenditure.
No additional search allowance is granted. One dispatch still permits at most
12 physical Ultra HTTP attempts; the task lifetime remains 72.

After a named repair, use the workflow `resume_reason` input (or CLI
`--resume-reason`) to reopen only the selected closed case. The operator reason,
commit and original result are retained. A given commit/reason can execute once;
it does not reset search, Extract, fetch or model allowances.

Located lexical candidates are presented through `review_passages`: Ultra keeps
relevant exact text or rejects irrelevant/header/duplicate candidates with a
reason before more reading. Both choices are recorded as acquisition decisions,
never truth verification. Delivered ranges are excluded from repeated model
reads, with new source versions and unseen continuations still allowed. Failed
discovered hosts named in critical source requirements are routed to the single
remaining basic Extract rescue, then targeted reading and passage selection.
The routing heuristic does not certify authority or source reliability.

```powershell
python -m ForecastAgent.debug_trial --root snapshots/runtime-collection-five-20261001 --question 43525
```

## Preserved-body repair

On resume, the runtime reparses saved gzip responses with the current document
reader. It verifies their original byte hashes, preserves capture timestamps,
archive provenance and previous parsed versions, and clears stale passage IDs.
No HTTP or search allowance is consumed. Exact excerpts must be located again
against the new parsed version. The original model conversation stays on disk.

Compressed downloads are decoded before HTML/PDF/CSV/JSON parsing. Expanded
bytes are bounded; unknown encodings and broken streams fail explicitly.
Corrupt binary-looking text cannot count as readable material or progress.
Archive reading uses the original `read_url`; replay URLs are provenance only.
CDX may return the same path with one directory slash added or removed, but
scheme, host, query and fragment must still match.

## Historical planning

### Current-information acquisition (current debugging default)

The single-case workflow defaults `current_information` to true. Select an
existing `question_id`; it will not migrate or run the other four tasks.
The explicit CLI equivalent is:

```powershell
python -m ForecastAgent.debug_trial --root snapshots/runtime-collection-five-20261001 --question 43525 --current-information
```

This records a one-way `collection_temporal_policy=current_information`
amendment, preserves the original request/hash and dates, and archives a closed
previous result before reopening that selected task. Search attempts, raw
captures and all lifetime quotas stay unchanged. Applying the same amendment
again does not reopen a closed task. Previously date-filtered saved leads and
captures can be reused without another provider request. Current HTML, PDFs,
datasets and market snapshots are permitted; search date filters, body date
masking and mandatory archive eligibility are disabled. Body readability and
capture integrity checks remain active. Exports explicitly identify current
information and cannot be treated as clean historical backtests.

New collection requests can set `collection_temporal_policy` to
`current_information`. Existing tasks retain their policy until explicitly
amended. Set the workflow input false to retain enforced historical acquisition.
For a multi-question dispatch with no `question_id`, this input must be false.

The historical cutoff is the simulated present, not the event resolution date.
Collect prior observations and then-available forward-looking drivers. Explicit
post-cutoff realized-price/value demands are rejected before freezing a plan.
This syntactic guard does not prove semantic adequacy or remove model-memory
leakage. The compact context exposes the deterministic Exa attempt state so
the model can distinguish an already-used allowance from a pending obligation.
