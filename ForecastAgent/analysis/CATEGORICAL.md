# Multiple-choice diagnostic

`ForecastAgent.analysis.categorical` adds an isolated typed diagnostic without
changing the immutable binary v8 baseline or enabling competition submission.
It consumes a saved evidence bundle, retains every exact Metaculus option,
checks quoted evidence against visible original text and requests a complete
probability distribution from the configured reasoning backend.

Ultra is primary. Two consecutive service failures switch to Super. Shared
quota/authentication failures do not switch models. Reservations are persisted
before HTTP; each reasoning backend has at most three lifetime attempts.
Mercury receives the qualitative analysis and the exact option registry, with
the reasoning probabilities withheld. Its choice response has one lifetime
request. Received HTTP responses can be replayed after a parse crash.

Both distributions are validated and their option-wise arithmetic mean is
reported. Rounding normalization is explicit and limited to 2% for Mercury.
Missing or duplicated options, invalid probabilities, invented quotes and
changed frozen inputs are rejected. A failed Mercury request preserves an
explicit single reasoning route; there is no uninformed default distribution.

The diagnostic saves a separate payload preview using bounded simplex
projection into [0.02, 0.98] per option, retaining a probability sum of one.
This does not modify the raw route or mean probabilities
and does not call a Metaculus submission endpoint. Model distribution outputs
have not been fitted or proven calibrated for forecasting.

The manual `Nonbinary forecasting diagnostic` workflow fetches full question
details using the existing bot token, removes outcomes and community forecasts,
and refuses missing resolution criteria. It checks the original collector for
an existing task before starting an isolated diagnostic ledger. Acquisition
uses the existing maximum three Tavily basic searches and one Exa search;
bounded free supplementation follows acquisition before typed analysis.
Every repeat restores the same artifact, with fail-closed missing-ledger checks.

These closed-question runs use current evidence without a historical cutoff.
They test collection and distribution handling, not historical accuracy or an
accepted competition forecast. Numeric, discrete and date diagnostics use
`ForecastAgent.analysis.range_forecast` and the shared durable typed executor.
The live competition queue's nonbinary scheduling remains unchanged.

## Real-case acceptance on October 2, 2026

The saved Fall snapshot contains six non-binary questions: two multiple-choice,
two numeric and two discrete questions. Post 45849 / question 46024 was selected
for the first multiple-choice diagnostic, with eight exact match-length options.

Authenticated detail responses, including an explicit `include_descriptions`
request, returned null description, resolution criteria and fine print. Bounded
public HTTP and Chromium navigation both returned HTTP 403. Runs 36973689602,
36974214770 and 36974735147 preserved these input failures; the latter two
restored the same preceding artifact. No Tavily, Exa or model request was made.

The rule reader recognizes only an explicit public Resolution Criteria heading
and checks the question title. It never infers rules from a title or other
market. It persists one HTTP and one browser attempt before execution. A verified
official rule snapshot is required to complete the real-case analysis. Passing
offline typed-output tests does not mean the live acquisition test passed.

The subsequent GET-only permission audit confirmed the current bot has enabled
API forecasting access but a restricted data tier. The official API policy
limits closed-question text to questions forecasted by the account, explaining
why missing closed-question rules do not establish a forecasting permission
failure. Format acceptance can use historical grid metadata without labels;
real evidence-grounded analysis still requires the question's official rules.

References:
- https://www.metaculus.com/api/
- https://www.metaculus.com/how-to-forecast/
- https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request
