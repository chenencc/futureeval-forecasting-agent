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
