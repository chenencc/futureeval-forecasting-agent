# Ten-question fresh acquisition and score validation

This manual experiment extends the five-case handoff check to ten different
question and material families. Inputs are selected before inspecting labels.
They do not overlap the current five repair cases; earlier unrelated project
experiments may have used some of them. They are not a pristine model holdout.

For each question:

1. Start a separately identified current-information acquisition with no saved
   search or capture imports. Use the V3 material agent and delivery-before-stall
   control, Ultra with the existing two-service-failure Super fallback.
2. Run the independent release supplement, including bounded free HTTP,
   Chromium and local document reparsing. Preserve its parent archive.
3. Freeze one immutable package. Both scoring arms receive exactly this package.
4. Score old and new packing with the unchanged Mercury decision registry.
   Alternate arm order by question. Record the first raw and clipped probability.
5. Probe one additional saved-body reread when at least 900 new coordinate
   characters fit. Record whether the unchanged production diagnostics would
   have triggered it. A probe score never silently changes the production choice.
6. Audit original spans, identities and actual HTTP journals before opening the
   separate labels. Report first, probe, final and production-policy metrics.

The first and reread comparison also uses the exact same eligible question
subset, so skipped rereads cannot manufacture an apparent improvement. If no
new original text fits, record the skip rather than treating the first response
as a second call. A failed second request preserves the first score. Missing
first scores remain missing, with no fabricated 0.5 forecast.

## Limits and restoration

Per question, retain the release limits: three Tavily basic searches, one Exa
search, eight initial source fetches, one basic Extract batch, two supplement
HTTP captures, two Chromium captures, and eight local reparses. Each acquisition
dispatch has twelve decisions, sixteen physical model attempts and 900 seconds.
At most three explicitly restored dispatches may execute a case; no automatic
recollection, provider budget renewal or workflow rerun occurs.

Mercury uses at most two HTTP attempts per packing arm (four per question,
forty overall), with 22,000 first-request and 28,000 second-request byte bounds.
The experiment uses two concurrent questions, no paid model fallback, no fitted
calibration, no Jev and no prediction submissions. Production is unchanged.

`retrieval_trial.yaml` runs the `full_chain_ten` choice on `dev_acquisition_v2`.
The ten job artifacts contain inputs, stage identities, original collection
ledgers, parent ZIPs, supplement state, package hashes, score requests, raw
responses and failure states. A later manual dispatch with `resume_run` restores
these exact bytes. Completed stages reuse their verified result.

```sh
python -m ForecastAgent.acquisition.full_chain_ten \
  --output snapshots/full-chain-ten --question-id 44925 --dry-run

python -m ForecastAgent.acquisition.full_chain_ten \
  --output snapshots/full-chain-ten --review snapshots/full-chain-ten-review.json
```

These resolved historical questions use current unrestricted web material and
may expose outcomes. The resulting scores are engineering diagnostics, not a
leakage-free historical forecast benchmark. Opening and closing timestamps are
copied from archived platform metadata; absent fine print is not inferred.

## Budget-censored handoff amendment

Initial run `37390438288` exposed a stage gate: the collector exported readable
raw captures with `status=collected`, but a program budget stop also marked the
result incomplete. The original pipeline blocked those snapshots before the
supplement. The stopped run preserved four executed case artifacts; the remaining
six cases never executed collection. Its one fully scored case is reused.

`full_chain_ten_resume` admits only collected, readable snapshots closed by an
explicit program budget/stall stop, with `execution_report.interrupted=false`.
It verifies the original pipeline identity, archives the exact native ledger,
records the original unresolved result and counters, then advances to the
existing supplement stage. It does not declare semantic completeness, rewrite
gaps, renew searches, or call the collection model again.

The original first/reread scoring code, question inputs, Mercury registry and
original protocol remain frozen. A separately hashed amendment records the
adapter and exact canceled parent run. Restoration refuses an empty ledger if
collection started without a recoverable artifact. A case may start fresh only
when its exact parent job proves collection never executed. Failed provider
transports and shared account quotas are not eligible for this handoff.
