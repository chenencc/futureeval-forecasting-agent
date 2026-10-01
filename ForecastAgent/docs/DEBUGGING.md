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

The historical cutoff is the simulated present, not the event resolution date.
Collect prior observations and then-available forward-looking drivers. Explicit
post-cutoff realized-price/value demands are rejected before freezing a plan.
This syntactic guard does not prove semantic adequacy or remove model-memory
leakage. The compact context exposes the deterministic Exa attempt state so
the model can distinguish an already-used allowance from a pending obligation.
