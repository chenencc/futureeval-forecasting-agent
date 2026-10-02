# Metaculus nonbinary forecast interface

Reviewed October 2, 2026. This reference does not enable submissions.

## Official sources

- API documentation and CDF generation examples: https://www.metaculus.com/api/
- Question types and range forecasting: https://www.metaculus.com/faq/
- Authoritative request validation: https://github.com/Metaculus/metaculus/blob/main/questions/serializers/common.py
- Question metadata and forecast representation: https://github.com/Metaculus/metaculus/blob/main/questions/models.py

## Multiple choice

The request field is `probability_yes_per_category`, a dictionary keyed by
the exact current option text. Cover every current option. Each probability
must be between 0.001 and 0.999 inclusive, and their sum must be one.
Inactive historical options must be absent or null. Recheck the current
option registry before constructing a submission preview.

The current categorical diagnostic already constructs this dictionary and
keeps raw model outputs separate from its probability-floor transformation.
It is a diagnostic, not a server-confirmed submission.

## Numeric, date and discrete

Use `continuous_cdf`, not a single expected value or median. Let N be the
question's `inbound_outcome_count`, with the server default of 200 when absent.
The CDF has N + 1 entries. Numeric/date questions normally use 201 entries;
do not hardcode 201 for discrete questions.

Each adjacent difference must be at least 0.01 / N and no greater than
0.2 * 200 / N. Differences represent inbound probability masses.

- Closed lower bound: first entry equals zero.
- Open lower bound: first entry is at least 0.001.
- Closed upper bound: final entry equals one.
- Open upper bound: final entry is at most 0.999.

The first entry represents probability strictly below the lower bound. Mass
at the lower bound belongs in the first inbound bucket. The final entry
includes outcomes at the upper bound. For discrete questions, map the ordered
outcomes using the question's own range and outcome count.

Honor `scaling.range_min`, `scaling.range_max`, `scaling.zero_point`, units
and open-bound flags. A non-null zero point indicates logarithmic scaling;
date scaling uses Unix timestamps. The official API guide includes percentile
interpolation and CDF standardization examples. Validate locally after any
interpolation, fusion or standardization.

## Planned application to ForecastAgent

Have the reasoning model produce an interpretable distribution or ordered
quantiles in real units. Let deterministic adapters convert the output to the
platform representation. Fuse like distributions on the same grid, not just
their medians, and preserve both raw distributions and adjusted previews.
Numeric/discrete/date adapters are implemented in
`ForecastAgent.analysis.distributions` and `ForecastAgent.analysis.range_forecast`.
They share durable typed execution with the categorical diagnostic, including
Ultra-to-Super routing, received-response replay and a one-request Mercury
decision stage. Mercury estimates cumulative event probabilities at fixed
thresholds while the reasoning model's numeric quantiles are withheld.

## Probability clipping policy

The operational payload policy is [0.02, 0.98]. Binary candidates are clipped
directly. Multiple-choice candidates use bounded simplex projection, preserving
the sum of one. A two-percent minimum is infeasible for more than 50 options;
such inputs fail explicitly instead of producing an invalid distribution.

For range questions, the policy applies to cumulative probabilities, not to
every individual outcome mass. Clipping is followed by bounded PMF projection
to preserve platform minimum/maximum increments, with a numerical margin for
the server's nine-decimal increment rounding. Closed CDF endpoints retain
their mandatory zero or one values; interior probabilities and open tails
obey the clipping policy. Raw route outputs and adjustment magnitudes remain
visible. This is an operational constraint, not fitted probability calibration.

API discrete scaling uses expanded bin-edge bounds. Prefer the platform's
`scaling.continuous_range`; do not mistake nominal outcome centers for CDF
grid locations. Numeric logarithmic interpolation occurs in internal scaled
coordinates. Date quantiles require timezone-aware ISO timestamps.

## Offline format acceptance

Run `python -m ForecastAgent.analysis.format_replay --output PATH`.
The committed fixture contains six historical question metadata records without
resolutions: two multiple-choice, two numeric and two discrete. Two clearly
labelled synthetic controls cover dates and logarithmic scaling. Synthetic
provider responses exercise structured parsing, fusion, clipping and durable
replay, without model HTTP requests, searches or forecast submissions.
These checks establish format compatibility, not factual forecast quality or
live model success. Separate binary shadow candidates use the same clipping
policy. Nonbinary automatic live collection/queue scheduling is not enabled
by this diagnostic implementation.

Use question IDs rather than post IDs in forecast payloads. Question groups
contain separately forecastable child questions. Keep reasoning comments and
forecast receipts separate. This documentation does not authorize a forecast
submission or a test submission.
