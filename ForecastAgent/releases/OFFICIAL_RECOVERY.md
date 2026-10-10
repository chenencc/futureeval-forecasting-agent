# Official worker recovery patch

Production release: 1.0.5. Operational tag: `v1.0.5-recovery.2`.

## Failure classes

- Saved source lookup failure: retry from preserved analysis input. Never recollect to repair a local URL alias.
- Unknown stage failure: bounded retry, then `needs_review` after three recoverable failures.
- Source identity, frozen input or hash conflict: block delivery and preserve the complete task.
- Provider lifetime cap or account quota: stop and surface required action. Never reset ledgers.
- Unknown submission outcome: reconcile platform readback before another POST.
- Accepted forecast: skip collection, analysis and delivery on later runs.

## Evidence identity

Readers resolve the exact saved URL first. A canonical alias is allowed only if all matching source versions agree. Distinct bodies are never merged. Document offsets and original hashes remain unchanged.

The legacy recovery path applies only to the diagnosed pre-submission lookup error. The latest exact parent evidence delta must match the checkpoint inventory before missing files are copied. All bytes are checked before the task is reopened once.

## Deadlines and reporting

Pending tasks remain ordered by deadline. Process timeouts reserve twenty seconds for delivery. An optional second decision call is omitted when no more than 150 seconds remain; the validated first decision is retained. This safeguard does not invent a forecast when the first decision is absent or invalid.

Checkpoint storage retains complete blocked, review and pending tasks. Task health is checked after state and receipts are uploaded. Blocked questions make the job fail visibly; unconfirmed imminent deadlines produce warnings. Public listener health includes attention and deadline risk separately from successful polling. The listener and worker share the same terminal states, including `needs_review`.

## Validation

Fault tests cover URL aliases, body collisions, exact identity, bounded retries, preservation, no duplicate delivery, review stops, deadline timeouts and retention of a valid first score. Linux release tests include 100 mixed surfaces, provider failures, descendant process termination and frozen analysis source identity.

Actual recovery run `38039474549` restored question 46137 and obtained HTTP 201 plus platform readback at 2026-10-10T08:55:10Z, before its 09:00 UTC deadline. The prior nine accepted submissions were preserved. Original analysis prompts, collection budgets and provider failover policy are unchanged.

These controls prevent a recoverable reader error from becoming a silent permanent block. They cannot guarantee delivery when credentials, providers, source integrity or the platform remain unavailable through the deadline.
