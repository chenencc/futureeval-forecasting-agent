# Bounded collection campaigns

The campaign scheduler supports one to 100 frozen questions. Prepare a new
campaign with `python -m ForecastAgent.collection_campaign prepare --root ROOT
--fixture FIXTURE --count 100 --raw-recall`. This performs local preparation only.
Use `run --root ROOT --limit 5` for each authorized live dispatch. Do not run this
command in offline validation: it invokes the configured providers. The default
concurrency is one active task and each dispatch selects at most five tasks.
Model identity, input fingerprint and search quotas are frozen. Existing roots
cannot be silently switched to another acquisition focus or used to reset quotas.

## States and recovery

`status.json` exposes normalized completion states alongside legacy queue states:
`pending`, `running`, `complete`, `complete_with_gaps`, `retryable_interruption`,
`terminal_failure` and `budget_exhausted`. A closed task with extraction or
discovery failures is not a quality pass and is not automatically reopened.
Raw acceptance is recomputed from original captures instead of trusting an agent's
success flag. Completion does not verify event truth or comprehensive recall.

Campaign and task locks prevent concurrent owners. Execution reservations are
saved before dispatch. Owner interruption preserves consumed provider reservations,
sets a retry time, and schedules untouched tasks first. Reconciliation rejects
lost consumed state, regressed counters, altered inputs and invalid transport
hashes/paths. Retries continue the same task directory; budgets are never reset.
Per-task executions remain capped at three, physical model attempts at 72,
Tavily basic at three, Exa at one, shared source requests at eight and Extract at
one batch. The campaign also reserves model room before starting another task.

## Errors and circuit breakers

Only classified transient failures are automatically eligible for retry.
Backoff is 30, 60, 120, 240 minutes, bounded at 360 minutes; task execution caps
still apply. Parameter/integrity errors and unclassified errors require operator
attention. Individual task failure does not prevent independent tasks running.
Two transport outages in one task, or provider failures across two consecutive
tasks, pause the remaining queue for 30 minutes. Discovery API failures count;
ordinary third-party page blocking is a collection gap, not a global outage.
Credential or provider billing/quota errors pause the batch until operator review.

After fixing the provider, `resume-provider --root ROOT --resume-reason REASON`
records a substantive operator reason and removes the batch pause without
changing task state, retry times or quotas. A terminal task additionally requires
an explicit focused repair through the existing `--question` and `--resume-reason`
interface. Repairs cannot exceed execution or provider limits.

## Batch acceptance and accounting

Status is saved after each task and includes bundle paths, states, request counts,
reported tokens, missing usage records, source counts, possible index shells,
unmatched identifiers, parsing gaps, failed sources/searches, termination reasons
and accumulated session time. Missing usage is unknown consumption, never zero
billed usage. Provider transport records remain on disk for independent auditing.
Campaign files are atomically replaced after flushing their data to disk.

Offline fault injection validates 100 synthetic tasks over 20 five-task dispatches,
idempotent completion, owner loss, budget rollback, classified retries, API outage,
credential recovery and single-task isolation with blocked network sockets.
These tests demonstrate controller behavior, not production throughput or real
intelligence quality. Before unattended 100-question operation, run authorized
live acceptance stages of 10 then 25 questions and inspect cost, duration and gaps.
