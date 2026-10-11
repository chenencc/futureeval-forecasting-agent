# Frozen release-pair outcome evaluation

This development tool evaluates an original-only / original-plus-map ablation.
The original release owns conditional rereading. Both arms must receive identical
original spans, question contracts, source qualifiers and decision registries.
The diagram is the only permitted difference. This is not a full production
worker comparison or proof that an acquisition policy improves recall.

## Two distinct clocks

`question.as_of_utc` is a declared reading context. It does not record when a
forecast was produced. New research calls retain a separate `client-clock.json`
with UTC start and end, request/response identities and the physical receipt hash.
Cached replies and old closed experiments never receive replacement timestamps.
The local client clock is not a server timestamp attestation.

For prospective diagnostics, **both final replies** must finish strictly before
the independently reviewed first public outcome. A scheduled resolution date is
not that time. Official prediction deadlines are a separate eligibility check.
Old receipts without actual call clocks remain historical diagnostics even when
their declared `as_of` predates the public event. No outcome means no metric.

## Read-only archive workflow

```shell
python -m ForecastAgent.research_loop.evaluate_release seal --root closed-case --parent frozen-parent.json --output external-seal.json
python -m ForecastAgent.research_loop.evaluate_release evaluate --seal external-seal.json --output external-evaluation.json
python -m ForecastAgent.research_loop.evaluate_release evaluate --seal external-seal.json --binding independent-outcome-binding.json --output external-labelled-evaluation.json
```

An optional `--official-status` on `seal` binds an archived platform status record.
The external seal is created once after the run; it is an integrity inventory,
**not preregistration**. It records every experimental file and the registered
parent without editing them. Use `research_loop.labels.bind` for the separately
archived outcome contract, source hash, exact rules/options/units and independently
reviewed public-outcome time. ID-only results are quarantined.

Evaluation independently decodes received Mercury replies with the release type
policy. It verifies durable identities, source hashes and offsets, same originals
and qualifiers, final-stage selection and complete decision heads. Gap-only or
failed mapped arms are explicit non-pairs. They cannot be counted as a second
independent forecast. Missing/changed files are quarantined, never repaired during
evaluation. No network calls, implicit retries or budget renewal are performed.

Binary Brier, multiclass Brier, discrete RPS and bounded numeric/date CDF loss
remain distinct metrics. Bounded loss cannot distinguish distances beyond an
open range. Repeated forecasts on the same question are repeated observations,
not extra independent questions. A valid binding proves archive identity; source
truth, model-memory leakage, semantic applicability and untouched validation
remain separate research requirements.
