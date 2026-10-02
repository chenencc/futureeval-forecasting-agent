# Official competition operation

The official monitor polls the Fall 2026 FutureEval tournament every ten minutes.
GitHub schedules can be delayed. A complete open-question scan triggers the separate
official competition worker. Archive errors are recorded without blocking a complete
open scan. Legacy business workflows and Codex campaign heartbeats are disabled.

The worker registers every open question and orders work by the earliest scoring or
closing deadline. Up to five tasks run per dispatch. GitHub serializes workers;
subsequent monitor snapshots recover the pending queue. Account identity, current
rules, permissions and deadlines are checked before collection, analysis and delivery.
Binary, categorical, numeric, discrete and date questions use the validated platform
distribution adapters. Unsupported metadata fails explicitly with preserved state.

Ultra is the primary reasoning model. Two consecutive service failures route to Super.
Shared account exhaustion, credentials and invalid requests do not activate fallback.
Mercury is optional: use the equal mean when both valid routes exist, otherwise the
valid reasoning forecast. Missing reasoning output blocks submission; no default
probability is substituted. Operational probabilities are clipped to 0.02–0.98;
closed CDF endpoints and platform bin constraints remain valid.

Collection preserves lifetime reservations: at most three Tavily basic searches,
one Exa search and three collection executions per question. Adopted live ledgers
retain their original counters. Historical evidence is never adopted as live input.
Supplementation runs in a separate, frozen overlay before automatic analysis.

Delivery uses the platform's atomic forecast-and-private-comment endpoint. A durable
reservation is saved before sending. Both the forecast and the private reasoning
comment must be read back before the task is marked accepted. An interrupted or
unknown delivery is reconciled without blindly posting again. Rejected deliveries,
rule changes and exhausted lifetime caps remain explicit operator-action states.

Every worker restores the newest exact completed artifact, including failed runs.
If a run reached execution but its state artifact is missing, recovery fails closed.
`futureeval-official-state` stores the campaign, raw captures, model journals,
analysis, candidates, failures and delivery receipts for 90 days. Local sync archives
these artifacts under `E:\metaculus_data` using the existing authorized sync service.

The repository variable `FORECAST_COMPETITION_ENABLED=true` authorizes the official
worker. This deployment represents the owner's explicit competition authorization.
Operators must not manually choose probabilities or rerun an open question because
they dislike its result. Do not reset ledgers or post duplicate submissions.
