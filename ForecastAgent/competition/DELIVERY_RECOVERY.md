# Bounded recovery of an atomic forecast and private comment

The original candidate, comment, receipt marker, acquisition budgets and analysis
are frozen. A known HTTP502/503/504 may be retransmitted at most twice after the
first POST, across all processes and dispatches. Generic lost responses without
a service status and acknowledged201 writes remain reconciliation-only.

Each retry requires two complete authenticated readbacks, separated by at least
30seconds, containing neither an own forecast nor the matching private comment.
The last POST must be at least120seconds old. Account identity, open permission,
own history and deadline are checked again immediately before transmission.
There is no atomic server idempotency guarantee; separated negative readbacks
reduce delayed-commit risk but cannot eliminate it. Partial or unreadable
receipts do not permit retry. Existing accepted receipts are never reposted.

`submission-initial.json` preserves the exact original receipt bytes. Cumulative
`post_attempts` and typed `readback_checks` explain every subsequent decision.
Bounded service-response diagnostics retain request identifiers and redact the
credential. No acquisition or analysis call is needed for delivery recovery.
