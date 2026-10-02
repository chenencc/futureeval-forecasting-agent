# Fall 2026 competition launch

## Objective and release boundary

Use the immutable ForecastAgent 1.0.0 analysis baseline while building a
separately versioned competition runtime. Keep all maintained code, schemas,
skills, tests and operational instructions under `ForecastAgent/`.

Tournament: `fall-futureeval-2026`.
Reference rules: https://www.metaculus.com/notebooks/38928/aib-resource-page/
Participation: https://www.metaculus.com/futureeval/participate/
Announcement: https://www.metaculus.com/notebooks/45615/announcement-of-futureeval-fall-2026/

The owner confirmed completion of the required participation form on
October 2, 2026. This is an owner attestation, not a platform registration
receipt. No
human may set, edit or selectively approve probabilities on competition
questions. Human involvement is limited to software and operational repair.
Publish the automatically generated reasoning comment as required by the
tournament, using the platform's appropriate visibility settings.

## Observed readiness on October 2, 2026

Monitor run `36970547378` captured eight question records at
`2026-10-02T05:48:00Z`, with complete open and archive scans. All eight were
closed: two binary, two multiple choice, two numeric and two discrete.
There were zero open questions at that observation time. Do not attempt to
backfill a closed forecast. This observation does not inspect the bot's
server-side forecast history.

Monitor and collector jobs were successful. The monitor polls every twenty
minutes; collection takes at most two binary tasks per invocation and does not
prioritize scoring deadlines. Non-binary questions are snapshotted but not
automatically collected. There is no validated production bridge to the v8
analysis or a durable, readback-confirmed submission ledger.

## Priority 0: durable competition queue

Discover every question, including upcoming questions, each group child and
rule updates. Maintain a ledger keyed by tournament, question ID and rule
revision. Store post ID separately from question ID.

Required fields include discovery time, open time, scoring deadline, current
close time, observed status, rule hash, release commit, stage, retries, provider
consumption, frozen evidence IDs, forecast payload hash and server receipt.
Queue stages:

`discovered -> queued -> collecting -> supplementing -> analyzing -> ready -> submitting -> accepted`

Explicit exception states: `retry_wait`, `unsupported_type`, `blocked_integrity`,
`submission_unknown`, `provider_blocked`, `deadline_missed` and `closed`.
An accepted forecast and a posted reasoning comment require separate receipts.
Seen, collected and forecasted must never be interchangeable states.

Schedule earliest deadline first. Recheck status and rules before inference and
immediately before submission. Use current API scoring metadata and close time;
do not infer a deadline from the title or scheduled resolution date. Record a
missing or contradictory deadline as an explicit operational gap.

Drain outstanding tasks independently of the newest monitor snapshot. One
failed task must not block later questions. Restore the exact latest durable
state and cumulative budgets; fail explicitly if that state is unrecoverable.

## Priority 0: live evidence and analysis bridge

Feed current collection artifacts into the analysis baseline through a
hash-verified live adapter. Record `as_of_utc`, analysis completion and platform
acceptance timestamps. Do not provide labels, retrospective bodies or later
captured pages to a pre-deadline forecast. The current historical campaign
workflow is not this adapter.

Trigger bounded supplementation between acquisition and analysis where useful.
Keep unavailable pages as gaps. Never replenish the original three-search
Tavily allowance or Exa allowance during stage changes or retries.

Reserve enough deadline time for a first forecast. Optional source enrichment
must not prevent an otherwise valid forecast from reaching submission.
Evidence incompleteness alone is not a malformed probability; integrity failure,
unknown references, invalid distributions and unvalidated numeric recovery are
separate automated gates. Implement these distinctions with generic schemas,
not question-specific repairs.

Fix the gap sentinel and AND/OR coverage representation in a new patch release.
Do not silently change the frozen v8 protocol or use observed outcomes to tune
weights for the same diagnostic sample.

## Priority 0: all question types

Implement typed forecasting adapters and validation for binary probabilities,
multiple-choice vectors and numeric/discrete distributions. Group children must
be accounted for individually. Reuse the analysis brain and tools while using
the exact Metaculus representation for each type.

Validate option order, sum-to-one, finite probabilities, distribution bounds,
CDF monotonicity, tails, units, open-bound endpoints and consistency of grouped
questions. A scalar binary probability is not a numeric forecast. Never silently
skip unsupported types or submit a fabricated uniform distribution to claim
coverage. Count them in the denominator and raise an operational alert.

## Priority 0: submission and recovery

First verify bot identity and permissions using read-only account endpoints.
Run a real submission and readback test in the official bot testing area after
the competition runtime is built. Avoid invoking the legacy template workflow
as a substitute for validating the release 1.0 brain.

Freeze a versioned automatic selection rule before live outcomes: paired valid
binary outputs use their equal mean; a single valid route may be used only under
an explicitly recorded fallback rule. Both unavailable means no model forecast.
Never publish the bookkeeping default of 0.5 as if generated by a model.
Convert boundary probabilities to the platform's documented permitted range
and record both native and submitted values. Do not invent a calibration.

Use a durable outbox keyed by bot ID, question ID and payload version. Record
the exact payload before its HTTP attempt. On a timeout, read back the bot's
forecast before deciding whether to retry: the server may have accepted it.
Readback must match the intended question, probability/distribution, acceptance
time and active bot. A successful job or HTTP status alone is insufficient.
Reconcile comments independently to avoid duplicate comments on retry.

## Priority 0: missed-question reconciliation and observability

Reconcile all eligible tournament question IDs against server-confirmed own
forecasts. Detect missing discovery, queued-but-unprocessed questions,
unsupported types, unknown submissions and missed deadlines separately.
Emit deadline warnings with remaining time and the blocking stage.

Keep GitHub Actions scheduling, a bounded worker and a recovery watchdog
independent. Verify the existing local watchdog's installation and recency;
its file being present is not evidence it is running. Do not rely on a twenty
minute cron guarantee. A reconciliation scan after a delay must be able to
drain the durable queue without resetting budgets or duplicating a worker.

Maintain raw artifacts and a cumulative ledger beyond the 90-day artifact TTL;
use the local `E:/metaculus_data` store for immutable copies and ensure a latest
cloud state remains available when this computer is offline. Show accepted
forecast coverage over all eligible question types, submission latency,
deadline misses, actual provider attempts, usage and unresolved failures.

## Activation acceptance gates

1. Required participation form confirmed; active bot identity checked.
2. Typed queue and exact deadline handling pass offline failure scenarios.
3. The official bot testing area accepts and returns the intended forecasts
   and reasoning comments for every supported question type.
4. Queue replay, timeout after server acceptance, artifact loss, provider
   failure and a rule update do not reset budgets or duplicate submissions.
5. Complete live discovery can be reconciled against the confirmed outbox.
6. Deployment is pinned to a reviewed immutable commit and has an explicit
   enable switch, rollback target and separate experiment storage.

Until these gates pass, report `production_ready=false`. Do not claim that
question snapshots or retrospective Brier establish an operational competition
bot. Full coverage cannot be guaranteed through platform outages, exhausted
provider quotas or missed historical deadlines; all such gaps must be visible.

## Git maintenance

Never move `v1.0.0`. Develop competition work on `codex/competition-launch` or
another scoped `codex/` branch. Publish tested patches as new immutable tags.
Keep retrospective diagnostics separate from prospective forecasts. Release
notes must identify inference protocol, scoring policy, routing policy, budgets,
tested commit and rollout results. Rollback uses a tag and the same durable
ledger, never a quota reset or a deletion of accepted forecasts.
