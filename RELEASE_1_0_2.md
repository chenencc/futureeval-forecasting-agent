# ForecastAgent 1.0.2

**New V3 material acquisition + independent supplement + unchanged release
1.0.1 Mercury original-evidence analysis.**

The release uses Ultra acquisition with the existing service-failure Super
fallback, Tavily basic search, Exa, free readers, and deterministic independent
supplement. It retains the release 1.0.1 Mercury first-read/conditional-reread
decision logic and the authorized reasoning-only fallback. The 47-file frozen
analysis baseline remains unchanged. Source integrity is additionally frozen in
`ForecastAgent/releases/manifest.json`.

## Reliability changes

- Persist the complete question inventory before external calls.
- Normalize grouped and conditional subquestions using exact child IDs and
  rules. Isolate bad rules, metadata and dates per question.
- Run each question in a child process, with a 1,500-second deadline and process
  tree termination; dispatch at most five within a 6,000-second batch budget.
- Export usable original material after a qualified budget/local close or
  completed, checksum-verified 5xx service-error receipts. Keep original raw
  files, failures and quotas unchanged, and explicitly include interruption gaps.
- Preserve a valid first Mercury score when rereading fails. Recover malformed
  provider responses only through exact journal replay, without renewing the
  Mercury attempt cap or treating source-integrity errors as service failures.
- Bind saved candidates to the exact analysis source and verify them again
  before delivery. Keep atomic forecast/private-comment readback, duplicate
  prevention and unknown-POST reconciliation from the existing release.

## Completed acceptance

[Successful GitHub Actions validation](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37398382595),
runtime commit `a4bcf247bdc55116e75eaf65f6951959c3416fd8`:

| Check | Result |
|---|---|
| Linux regression tests | 544 passed |
| Offline load test | 100/100 scored; 20 each binary, multiple choice, numeric, date and discrete |
| Offline queue draining | 20 batches of five; all IDs accounted for |
| Duplicate simulated submissions on replay | 0 |
| Real provider compatibility | 5/5 scored |
| Original collection files preserved after recovery | 4/4 native cases; all bytes identical |
| New collection/search calls during recovery | 0 |
| Real competition submissions during validation | 0 |

The real-provider pilot contains four native historical cases with fresh full
collection and independent supplement, plus a synthetic saved date case with
real Mercury calls for CDF compatibility. Archived date examples lacked
authoritative API scaling, so the date case is explicitly a format probe.
The numeric case also completed the conditional reread route. The other four
used the valid first-read route.

The [initial run](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37397222819)
exposed Ultra service interruptions and a cross-ledger digest mismatch. The
successful run restored every exact original artifact, recovered the three
failed cases, and retained the two existing candidates unchanged. It did not
restart collection or search budgets.

Fault tests exercise actual timeout/descendant termination, isolated provider
outage, grouped/conditional identity, bad metadata, infeasible category floors,
malformed reread recovery, corrupt saved state, capped retries, and lost POST
responses across all five distribution types.

## Use and operational limits

See [the runtime README](ForecastAgent/releases/README.md). Inventory-only:

```sh
python -m ForecastAgent.releases.v1_0_2 --root snapshots/official-1.0.2 --snapshots snapshots/tournament
```

Authorized production execution explicitly adds `--submit`. Restore the exact
previous artifact before resuming on another runner. Existing authenticated
forecasts skip every paid stage. Attention states are explicit when credentials,
quota, all providers, missing platform metadata or a deadline prevent a valid
forecast; the worker does not invent a default score.

Binary forecasts retain the 0.02–0.98 clip. Categories retain normalization and
exact labels, with the same bounded probability policy; more than 50 categories
cannot satisfy a 0.02 per-category floor and are explicitly blocked. Range/date/
discrete forecasts retain authoritative CDF grid and endpoint rules.

These are engineering acceptance results. Current web content on resolved
questions and model knowledge can reveal outcomes; they are not an independent
forecasting-accuracy benchmark. The 100-case HTTP boundary is simulated, and the
real-provider pilot made no Metaculus submission.

Publishing this release does not switch the existing production worker. Its
current `main` workflow remains explicitly pinned to `v1.0.1`; this task has not
changed the cron scheduler or enabled another competition worker.
