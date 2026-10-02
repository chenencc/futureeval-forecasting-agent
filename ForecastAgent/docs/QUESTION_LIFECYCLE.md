# Official question discovery and lifecycle

The Fall 2026 primary tournament starts opening questions on September 28, 2026.
The announcement says the first one to two weeks will be slow. A running tournament
does not imply that at least one question is accepting forecasts at every instant.

Authoritative child `question.status` has four values:

| Status | Meaning | Accept a new forecast? |
| --- | --- | --- |
| `upcoming` | Forecasting has not opened | No |
| `open` | Forecasting window is open | Subject to current bot permissions |
| `closed` | Forecasting ended, resolution pending | No |
| `resolved` | Official resolution recorded | No |

Post curation (`approved`, `pending`, etc.) is a separate publication layer.
Grouped posts require checking each child; a post status cannot replace child status.
A null resolution value must not be used as a substitute for status because API
access restrictions can hide historical resolution values.

`open_time` starts the forecasting window. `scheduled_close_time` and any earlier
`actual_close_time` bound submission availability. `spot_scoring_time` is the scoring
instant, not an event resolution date. `scheduled_resolve_time` estimates when an
outcome will be determined; passing it does not itself resolve the question. The
worker's internal competition deadline includes the scoring instant separately
from the platform submission deadline.

Recent observed formal Fall questions had three-hour forecasting windows. Their
events can resolve months later. Three hours is an observation of these questions,
not a hardcoded universal window. Always use the actual timestamps.

Discovery follows the official bot template's `/api/posts/` polling approach:

```
GET /api/posts/?tournaments=fall-futureeval-2026&statuses=open&limit=100&include_descriptions=true&with_cp=false
GET /api/posts/?tournaments=fall-futureeval-2026&limit=100&include_descriptions=true&with_cp=false
GET /api/posts/{post_id}/?with_cp=false
```

Authenticate as the bot. The slug and project ID `33121` identify the same tournament.
Follow `next` pagination until exhausted; do not use only the first page. A list
response may cut grouped questions to three children, so the monitor reads each
group's detail before marking a scan complete. Ignore notebooks as forecasting
tasks. Persist stable child question IDs; newly discovered is not the same as
currently open. Refresh status, rules, bot permissions and deadlines before submitting.

The primary FutureEval tournament uses spot peer scores. The last eligible forecast
counts; continuous updating is not required. The Fall announcement's continuous
updating requirement belongs to Market Pulse. Avoid transferring rules between
these tournament series.

Sources:
- https://www.metaculus.com/notebooks/45615/announcement-of-futureeval-fall-2026/
- https://www.metaculus.com/notebooks/38928/bot-tournament-resources-page/
- https://github.com/Metaculus/metac-bot-template/blob/main/main_with_no_framework.py
- https://github.com/Metaculus/metaculus/blob/main/questions/models.py
- https://github.com/Metaculus/metaculus/blob/main/posts/services/feed.py
