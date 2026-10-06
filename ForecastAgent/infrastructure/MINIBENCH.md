# MiniBench production entry

MiniBench uses the existing registered Metaculus bot, ID 309450. The participant
previously confirmed completion of the shared FutureEval participation form.
There is no separate per-round enrollment operation in the published setup:
the bot enters by forecasting eligible questions through the Metaculus API.
Prize eligibility remains subject to the organizer's verification.

## Deployment

- `minibench_monitor.yaml`: authenticated complete open-question scan using
  `tournaments=minibench`, plus archive snapshots and per-round lifecycle data.
- `minibench_competition.yaml`: immutable `v1.0.3-minibench.1` routing extension
  of v1.0.3, original collection, independent supplement and Mercury rereading.
- Fall remains pinned to its original `v1.0.3` tag and its existing ledger.
- `FORECAST_MINIBENCH_ENABLED=true` enables automatic MiniBench submissions.
- `MINIBENCH_LISTEN_ENABLED=true` in `futureeval-monitor` adds MiniBench to the
  existing external cron gate. No second cron schedule is needed.
- Both workers share one concurrency group. The public gate dispatches at most
  one monitor per tick, alternating tournament priority every ten minutes.
- Secrets are reused by name; no credentials are copied into source files.

## Preservation and enrollment evidence

MiniBench has separate monitor, question, state, control and evidence artifacts.
Every campaign identity must equal `minibench`; a Fall checkpoint is rejected.
Question IDs remain global across rounds. Existing platform forecasts and saved
accepted receipts suppress repeat collection, scoring and submission.
The rotating `minibench` slug follows the active round without erasing earlier
receipts. Closed questions are never submitted retroactively.

Limits remain five questions per dispatch, at most three lifetime Tavily basic
searches and one Exa search per question. Model selection, score clipping,
private comments, atomic submission and readback confirmation are unchanged.
The routing extension changes only the live campaign identity constants; its
AST restoration receipt proves the remaining live module is unchanged.

Run Linux acceptance before enabling the public gate. A successful empty scan
proves connectivity, not an accepted forecast. Claim active competition entry
only after a forecast and private comment have matching platform readbacks.
Existing local artifact sync archives both tournament workflows automatically.

References:
- https://www.metaculus.com/aib/minibench/
- https://www.metaculus.com/notebooks/38928/docs.adj.news
- https://www.metaculus.com/tournament/minibench/
