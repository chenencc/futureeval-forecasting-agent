# Unified acquisition analysis pilot

## Frozen execution

- Acquisition parent: run `37267803362`, artifact `11326758690`.
- Analysis run: [37269591387](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/37269591387).
- Execution commit: `4badba3` on `dev_formal`.
- Retained analysis: `mercury-evidence-chain-v2`, `inception/mercury-decide:free`.
- Five original body packages reused; zero new searches, collection calls or submissions.
- Labels: separate local ForecastBench resolved Metaculus checkpoint records.
  Their provenance is in `fixtures/repaired_fifty_evaluation_labels.json`.
- Source text can contain outcomes. This is a retrospective diagnostic, not a
  leakage-free forecasting benchmark or competition score.

## Results

All five forecasts completed. Eight Mercury HTTP attempts reported 412,096 total
tokens, with zero unknown-usage attempts. Three questions received a conditional
second read. The first/second request ceilings were 22,000/28,000 encoded bytes.
No failed V7 selection gate prevented the gasoline question from receiving a score.

| Question | Clipped P(YES) | Recorded outcome | Correct at 0.5 |
| --- | ---: | --- | --- |
| 43494: Data-center grid restrictions | 0.037327 | YES | No |
| 43501: Another Instructure hack | 0.980000 | NO | No |
| 44801: US gasoline above $5.016 | 0.020000 | NO | Yes |
| 36871: Combined USDT/USDC market cap | 0.952574 | NO | No |
| 43991: Le Pen eligibility | 0.020000 | NO | Yes |

| Metric, matched five questions | Earlier repaired capture run 37106094306 | Fresh unified packages |
| --- | ---: | ---: |
| Accuracy | 80% (4/5) | 40% (2/5) |
| Brier, lower is better | 0.189971 | 0.559067 |
| Natural-log loss, lower is better | 0.701775 | 2.057811 |

Bodies, capture dates and question envelopes differ. This same-question comparison
is exploratory, not a controlled A/B experiment. Four of the five labels are NO;
an always-NO classification would achieve 80% on this deliberately small cohort.

## Concrete remaining defects

1. **Relevant saved rows omitted from the inference envelope.** The June 30 CMC
   historical snapshot contains USDT at $184,368,461,962.97 and USDC at
   $73,360,220,193.95. Neither row appears in the final Mercury evidence. Only the
   historical page's navigation/header spans E0022/E0023 are visible. The stated
   date-endpoint total alone does not establish an interval maximum; however,
   omitting these observations is a demonstrable analysis reading defect.
2. **An unresolved opening context receives confident support.** The Instructure
   rule requires a new incident after opening. The frozen package lacks the opening
   timestamp. Visible reports describe April/May incidents; the model assigns
   88.6% support to the timing condition and 99.88% raw event probability without
   closing that missing context. A specific cause cannot be established from the
   decision heads alone.
3. **Coverage diagnostics do not make the event estimate coherent.** The data-center
   response leaves timing, scope and observation coverage unresolved but assigns
   3.73% event probability. The stablecoin response leaves those conditions unresolved
   but assigns 95.26%. Conditional rereading preserves these gaps; transport success
   does not mean semantic acceptance. Headers and background occupy limited context
   that could instead deliver operative provisions and observations.

All raw bodies remain intact. The archive was imported into `E:/metaculus_data`
with matching SHA-256. The English audited report is
`reports/unified-analysis-five-37269591387.json`. It contains provider usage,
predictions, diagnostics, exact visible character counts, label provenance and
the matched earlier probabilities. Production has not been changed.
