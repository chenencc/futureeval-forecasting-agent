# Local budget recovery (market-v0.3.5-budget.1)

The daily local model ceiling is 1500 physical reservations per UTC day. This is
not a statement about OpenRouter account availability. Failed transmissions and
unknown physical reservations remain charged. The per-task limit stays 60;
the dispatch limit stays 200; all search and admission limits are unchanged.

Before entering collection, the worker checks local model capacity. A denial
before transmission is stored separately from `provider_reservations`. Daily
holds wait until UTC midnight. Dispatch holds yield to the next serialized
worker. Lifetime task exhaustion stays visibly blocked. A budget hold does not
consume a service execution; historical execution counters are never erased.

Native collectors can wrap exceptions. The outer worker retains the typed
denial receipt and classifies incomplete output as `budget_wait`, rather than
attempting to read a nonexistent package. Non-budget incomplete output has an
explicit `CollectionIncomplete` error and keeps the original native report.

Legacy recovery requires a hash-verified native `ProviderCap` receipt in the
last failed execution, a saved missing-package error and no physical provider
reservation in that execution. Only that proven execution is excluded from
service retries. Original failures, requests, bodies, local call journals and
physical reservation records remain intact. The effective native remaining
allowance excludes only proven non-HTTP denials; an independent receipt records
both original and effective counts. No search reservation is refunded.

The exact 400-to-900 and 900-to-1500 policy migrations are audited. Pending tasks
from the exact market-v0.3.4 and market-v0.3.5 predecessors may resume only if the pinned collection engine and
all listed capture/search/scoring stage files match their frozen digests.
Their original task deployment identities and forecast configuration remain
preserved. Production competition controls are independent.

Validation includes zero-HTTP recovery gates, pinned-engine integration and a
Linux replay of the full saved production checkpoint. A successful replay is
not proof of a completed forecast; actual worker publication is checked later.
