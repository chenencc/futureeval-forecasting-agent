# External prediction-market scheduler

The scheduler sends a small authenticated GitHub dispatch request. The GitHub
runner still performs discovery, acquisition, analysis and price tracking.

```text
cron-job.org (every 10 minutes)
  -> market_research_monitor.yaml on main
  -> Market repricing research worker on successful monitor completion
  -> saved evidence, forecasts and creation-based 1/3/7-day price labels
  -> existing local artifact synchronization
```

## GitHub access

Reuse the existing scheduler token if it has access to this repository and has
not expired. Otherwise create a fine-grained personal access token in GitHub
Settings, Developer settings, Personal access tokens, Fine-grained tokens:

- Resource owner: `chenencc`.
- Repository access: only `futureeval-forecasting-agent`.
- Repository permission: **Actions: Read and write**.
- Choose an expiration date and retain a reminder to renew it.

Enter the token directly into cron-job.org. Do not place it in a repository file,
URL or chat. The GitHub dispatch endpoint requires Actions write permission.

## cron-job.org job

Open <https://console.cron-job.org/jobs/create> and use:

| Field | Value |
| --- | --- |
| Title | `Prediction markets - discovery and price tracking` |
| URL | `https://api.github.com/repos/chenencc/futureeval-forecasting-agent/actions/workflows/market_research_monitor.yaml/dispatches` |
| Enabled | Yes |
| Schedule | Every 10 minutes |
| Timezone | `Asia/Shanghai` |
| Request method | `POST` |
| Request timeout | 30 seconds |
| Basic HTTP authentication | Off |

Under the advanced request settings, add these headers:

| Header name | Header value |
| --- | --- |
| `Authorization` | `Bearer YOUR_GITHUB_TOKEN` |
| `Accept` | `application/vnd.github+json` |
| `Content-Type` | `application/json` |
| `X-GitHub-Api-Version` | `2026-03-10` |

Replace `YOUR_GITHUB_TOKEN` with the actual GitHub token. Keep one space after
`Bearer`; do not include angle brackets or quote characters. The token is a
GitHub token, not a model-provider or cron-job.org API key.

Request body:

```json
{"ref":"main"}
```

Leave worker inputs empty. Dispatch the monitor only; its successful completion
automatically starts the existing worker through `workflow_run`. The worker
restores the exact monitor checkpoint and processes pending tasks. Both market
workflows continue to select the immutable release in `MARKET_REPRICING_SHA`.

## Verify the handoff

1. Save the job and run one test execution.
2. A successful GitHub dispatch returns HTTP `200` with a run ID for API version
   `2026-03-10`; older API versions can return `204`. A `2xx` response confirms
   only that GitHub accepted the dispatch.
3. Open <https://github.com/chenencc/futureeval-forecasting-agent/actions/workflows/market_research_monitor.yaml>.
   Confirm a new `workflow_dispatch` run on `main`, then inspect its report.
4. Confirm the follow-on worker run and its checkpoint. An empty eligible queue
   legitimately has no acquisition or model calls. Discovery pagination can be
   incomplete even when the workflow is green; inspect `discovery_complete`.
5. Confirm two later scheduled cron executions produce corresponding new GitHub
   runs. Keep the external job enabled to capture the delayed price windows.

The workflow remains enabled in GitHub. Disabling the workflow itself would also
block external dispatches. Its built-in GitHub `schedule` trigger is removed.

## Admission policy

The current production policy admits at most **30 event families per Shanghai
calendar day**, shared across Polymarket and Kalshi, with a maximum creation age
of two hours. Failed tasks still count toward their original admission day.
Repeated monitor checks do not restart task quotas or re-enroll the same family.
The cap is a maximum, not a quota to fill with unsuitable markets. Existing
per-task acquisition and model limits continue to apply.

References:

- <https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event>
- <https://docs.cron-job.org/rest-api.html>
