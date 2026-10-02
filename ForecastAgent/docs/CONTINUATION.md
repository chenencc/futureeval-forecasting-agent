# Frozen queue continuation

The raw campaign workflow supports explicitly authorized `continue_remaining`
execution. Each worker processes at most five untouched questions, writes its
continuation decision and uploads the entire preserved state before dispatching
one child worker. The child restores that exact completed parent run rather than
guessing which artifact contains its consumed budgets. Workflow concurrency
allows only one campaign worker at a time.

Classified interruptions are retried only after pending work, with existing
task execution limits and quotas. Provider backoff deadlines are retained.
Credential/quota review, missing state, unknown active ownership and exhausted
campaign budgets stop continuation. A closed task is never silently reopened.
No forecasting or submission capability is added.

The fixed queue ending with gaps is an acquisition closure, not verified event
coverage or forecast accuracy. The existing local sync imports each completed
artifact into the local data archive while later batches run on GitHub.

## Model routing

Each new dispatch prefers free Nemotron 3 Ultra. Two consecutive service failures
(transport failures, unavailable model, server errors or missing choices) switch
the remaining requests in that dispatch to free Nemotron 3 Super. A successful
Ultra response clears its failure streak; a Super recovery does not immediately
switch back. The next dispatch starts with Ultra again.

Shared account rate limits, authentication failures and invalid requests do not
trigger fallback. Model transport records include the actual requested model and
the routing reason. Every physical attempt still uses the existing reservation,
dispatch ceiling and lifetime budget. Backend policy migration is recorded in the
campaign ledger and never resets search quotas or opens closed tasks.
