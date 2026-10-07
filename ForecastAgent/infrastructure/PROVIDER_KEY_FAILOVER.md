# Production credential failover

The official worker runs immutable `v1.0.5` through an independent transport from
`main`. Forecasting prompts, models, task reservations and submission rules remain
defined by the immutable release. The transport composes the existing Exa wrapper.

## Credentials

| Provider | Primary Actions secret | Backup Actions secret | Runtime variables |
| --- | --- | --- | --- |
| OpenRouter | `OPENROUTER` | `OPENROUTER2` | `OPENROUTER_API_KEY`, `OPENROUTER_API_KEY2` |
| Tavily | `TAVILY_KEY1` | `TAVILY_KEY2` | `TAVILY_API_KEY`, `TAVILY_API_KEY2` |
| Exa | `EXA_API` | `EXA_API2` | `EXA_API_KEY`, `EXA_API_KEY2` |

Only configured, distinct backup credentials are eligible. Never print or commit
credential values. Local callers can use the same wrapper and environment names.

## Routing policy

OpenRouter interception covers POST `/api/v1/chat/completions` and
`/api/alpha/decisions`. Tavily interception covers POST `/search` and `/extract`.
Exact HTTPS host, endpoint and primary Bearer authentication must match. Other
traffic passes through unchanged.

- OpenRouter: explicit HTTP 402 insufficient credits or per-key credit exhaustion;
  HTTP 429 explicitly identifying daily quota exhaustion.
- Tavily: HTTP 432 plan usage limit and HTTP 433 pay-as-you-go limit.
- Exactly one backup attempt uses the original payload, model, options and timeout.
- Authentication, permissions, validation, generic rate limits, service failures,
  timeouts and unknown outcomes never switch credentials at this layer.
- OpenRouter in-flight spending holds (`openrouter_in_flight_budget`,
  `weight_exceeds_budget`, or HTTP 402 with `Retry-After`) do not switch.
- Errors inside a successful HTTP response, including partial generation, never
  switch: retrying such an outcome could duplicate paid generation.

Each exhausted role is remembered. Daily OpenRouter limits retry at the next UTC
day; Tavily plan limits retry at the next UTC month. Adjustable credit/spend caps
retry after one hour so a top-up can recover. Either credential's rotation
invalidates the old routing decision. If both roles are exhausted, calls fail fast
until a recorded retry deadline. Two OpenRouter keys may share the same account
quota; switching does not create additional quota.

After **more than three** successful fallback requests (the fourth), the working
alternative becomes the persistent default for that provider. Each observation
requires the old default to have a confirmed quota rejection or to remain inside
its recorded exhaustion cooldown, and the alternative to return HTTP 200. Cached
exhaustion is not counted as an additional HTTP failure; no blocked key is probed
just to reach the threshold. Ordinary HTTP service or authentication failures
do not authorize promotion. An unsuccessful fallback or a recovered default
breaks the streak. The promoted default persists across dispatches and calendar
resets. If it later exhausts its quota, the other key remains eligible and can
be promoted through the same rule. Credential rotation resets route preference.
The transport changes routing, not the stored Actions secrets. Exa keeps its
existing monthly failover policy.
HTTP 200 confirms request acceptance here; the immutable provider still validates
response format and forecast content independently.

## Accounting and recovery

`snapshots/official/provider-transport/<provider>/route.json` stores credential
digests and retry deadlines. `attempts/*.json` records each real HTTP attempt,
its credential role, status, payload hash, duration and logical transport ID.
Credentials, prompts, query text and response bodies are not stored there. Usage
is `unknown` at the transport layer; original release receipts retain reported
usage. Count transport receipts to audit actual HTTP attempts, rather than adding
them to logical release reservation counts.
`promotions/*.json` records default-role transitions and their successful-request
count without credential contents.

The storage splitter preserves these files in the resumable checkpoint. Failover
never changes task search limits or resets model/provider budgets. Child Python
processes inherit an opt-in `.pth` bootstrap even if they replace `PYTHONPATH`.
The bootstrap only activates when `FORECAST_PROVIDER_FAILOVER_MODULE` is set.

## Verification

Run `python -m unittest ForecastAgent.infrastructure.test_provider_failover
ForecastAgent.infrastructure.test_exa_failover ForecastAgent.tests.test_exa_search
ForecastAgent.infrastructure.test_storage_monitor -v`.

The **Provider key failover offline acceptance** workflow checks Linux behavior
and argument forwarding into immutable v1.0.5 without provider calls or forecasts.
An official worker with `verification_only=true` checks real secret injection,
transport installation, state restore and snapshot persistence without inference
or submission. Simulated exhaustion proves routing behavior; an idle production
run does not prove that real account exhaustion has occurred.

Official references:
- [OpenRouter limits](https://openrouter.ai/docs/api_reference/limits)
- [OpenRouter errors](https://openrouter.ai/docs/api_reference/errors-and-debugging)
- [Tavily Search errors](https://docs.tavily.com/documentation/api-reference/endpoint/search)
