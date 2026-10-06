# ForecastAgent 1.0.3

Production promotion of the patched 1.0.2 acquisition candidate. The 47-file
release 1.0.1 analysis core remains unchanged. The 1.0.2 tag is retained.

## Acquisition fixes

- Normalize observed URLs without corrupting query parameters.
- Validate individual batched tool arguments before network requests.
- Exclude navigation-only bodies and code search shells from readable evidence.
- Recover observed unattempted source leads before a stalled collector closes.
- Route independent supplement to unread known sources, retaining explicit gaps.

All original reservations and provider journals are retained. The limits remain
three Tavily basic searches, one Exa search and eight initial free HTTP captures;
independent supplement retains two HTTP captures, two browser captures and eight
local reparses. Readable acquisition does not certify relevance or sufficiency.

## Production execution

Use `python -m ForecastAgent.releases.v1_0_3 --root snapshots/official
--snapshots snapshots/incoming --limit 5 --submit`. Omit `--submit` for inventory
verification without model calls or forecast submission.

The worker retains the existing official campaign and confirmed receipts.
Terminal tasks are never reopened. Old in-flight acquisition is isolated for
explicit migration rather than receiving a fresh quota. Each question runs in
a bounded child process; later questions continue after an isolated timeout.

The external listener and monitor remain the scheduling source. There is no new
GitHub schedule. The production workflow checks out the immutable `v1.0.3` tag
and uses its release entrypoint, with independent main infrastructure utilities.
