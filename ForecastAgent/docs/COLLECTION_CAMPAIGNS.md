# Current-information collection campaigns

The campaign controller schedules up to 100 frozen acquisition questions. It does not forecast, submit, resolve questions, or restore historical information cutoffs. Original fixture dates are retained as provenance in `original_inputs.json`; resolved questions may contain current outcome information. These runs are acquisition engineering tests, not forecasting backtests.

Each dispatch handles one to five questions. Every question retains its existing three Tavily basic attempts, one Exa attempt, source snapshots, model transport reservations and execution history. Closed tasks are preserved. A closed package with gaps is distinct from an acquired package. Missing consumed state fails closed rather than creating new budgets.

The campaign initially permits 16 model HTTP attempts per selected question in aggregate, with at most three executions per task. The underlying engine still applies its 16-attempt dispatch limit and 72-attempt task lifetime limit. The queue reserves room for a complete engine dispatch before starting another question. Retries therefore consume room that could otherwise serve unstarted questions; increasing a frozen campaign budget requires a separate audited amendment and is not implemented here.

New tasks have priority over retries. Two provider 429/5xx errors in a task dispatch pause the campaign for 30 minutes. Interrupted tasks retain their reserved execution and all consumed provider attempts. Usage without a provider token report is counted as missing usage, not assumed to be zero.

`collection_campaign.yaml` restores the latest complete state artifact and refuses fresh budgets if a prior acquisition run lacks recoverable state. The workflow defaults to one question and has no automatic schedule. It does not dispatch the whole queue automatically. Downloaded artifacts can be imported with `ForecastAgent.local_sync` into the local content-addressed archive.

## Commands

```text
python -m ForecastAgent.collection_campaign prepare --root snapshots/example --fixture ForecastAgent/fixtures/historical_2026_135.json --count 100
python -m ForecastAgent.collection_campaign run --root snapshots/example --limit 1
python -m ForecastAgent.collection_campaign status --root snapshots/example
python -m ForecastAgent.collection_campaign run --root snapshots/example --question 44925 --resume-reason "Validate repaired inventory delivery on the preserved task"
```

Runtime credentials are `OPENROUTER_API_KEY`, `TAVILY_API_KEY` and `EXA_API_KEY`. The configured model is frozen for the campaign. State files include the queue manifest, original inputs, per-task bundles, raw source bodies, model request/response files, and a dispatch status report. A successfully uploaded artifact does not imply complete intelligence coverage or forecast accuracy.

A focused repair can bypass a selected incomplete task's retry delay with an explicit recorded reason. It does not bypass the campaign transport pause, reopen a closed package, replace inputs or reset provider budgets. The same code/reason operation cannot run twice automatically.
