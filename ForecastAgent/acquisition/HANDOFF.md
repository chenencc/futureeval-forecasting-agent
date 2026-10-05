# Saved-material handoff

This opt-in, offline tool repairs the first analyst request's original-text
delivery. It does not search, fetch, supplement, analyze, score, or submit.
Production competition wiring and the frozen release analyst remain unchanged.

## Contract

`handoff.pack(bundle)` returns `(state, manifest)` under the existing Mercury
first-request ceiling of **22,000 serialized request bytes**, including the
unchanged question registry. The model name, question, registry and analyst
instruction stay unchanged. This is a first-request export, not an automatic
replacement for the release's conditional rereading runner.

- Protect every original character visible in the release selector's first
  request. Coalesce only overlapping or adjacent spans in the same saved view.
- Reference URLs and body hashes once through `sources[source_id]`, rather than
  repeating them on every evidence span. Evidence coordinates remain exact.
- Validate banked excerpts against the original raw source hash, parsed-view
  hash, coordinate space, document index and exact slice. Invalid or stale
  references are recorded and cannot be admitted as banked material.
- Map a document into body coordinates only when the complete saved document
  occurs exactly once. Otherwise retain `coordinate_space: saved_document` and
  the original one-based `document_index`.
- Bind document views in the request to exact document text hashes. The sidecar
  retains raw capture and parsed-view hashes, original coordinates and mapping.
  Table extraction warnings remain attached to document-view metadata.
- Use small whole-line identity passages from the first 1,800 body characters.
  Rank navigation using proposition/rule terms and local inverse frequency;
  background menu vocabulary and agent bookkeeping cannot dominate this step.
  Identity text is not an inferred publication date or a verified table date.
- Select complete saved table lines together with their own column header.
  Rank payload rows using proposition/rule terms. No values are reinterpreted,
  cells corrected or absence claims generated.
- Expose omitted ranges and byte-limit reasons, rather than claiming that a
  saved or banked passage was read by the analyst. Overlapping banked ranges are
  counted once within each source/view of each bundle.

No acquisition adequacy declarations, chosen need IDs, model conclusions,
previous probabilities, resolution labels or rubric anchors enter `state`.
Source text remains untrusted data. Source/reference validation cannot certify
relevance, truth, event timing or complete observation coverage.

## Commands

```sh
# Package an existing acquisition or supplement-overlay bundle. No API key.
python -m ForecastAgent.acquisition.handoff \
  --bundle saved/bundle.json --output snapshots/handoff/first-request.json

# Compare old/new delivery on the SAME frozen bundle, for both collection arms.
python -m ForecastAgent.acquisition.handoff_replay \
  --root snapshots/intelligent-frontier-review-37316638264 \
  --output snapshots/intelligent-v1/materials-handoff-review-v3.json \
  --states-root snapshots/materials-handoff-v3

python -m unittest ForecastAgent.tests.test_material_handoff -v
```

Existing output files are immutable: changed implementation/input identities
require a separate output path. The paired audit verifies all 47 frozen release
dependencies, ten bundle checksums, the source pool and the frozen rubric. It
blocks accidental model/HTTP provider calls and audits exact source coordinates.

## Development replay: run 37316638264

The old/new comparison is paired inside each saved bundle. The original
collection control and candidate arms remain different stochastic realizations.
These five cases were used for repair, not held out. Three offline packing
revisions were inspected; the final revision fixes the known delivery failure.
This is development regression evidence, not a one-shot blind experiment.

| Saved collection arm | Old visible checks | New visible checks | Lost checks |
| --- | ---: | ---: | ---: |
| Control collection | 8/15 | 10/15 | 0 |
| Candidate collection | 8/15 | 13/15 | 0 |

| Question | Control old/new | Candidate old/new |
| --- | ---: | ---: |
| 36871: stablecoin capitalization | 0/3 -> 1/3 | 0/3 -> 3/3 |
| 43494: data center restrictions | 2/3 -> 2/3 | 2/3 -> 3/3 |
| 43501: Instructure incident | 0/3 -> 1/3 | 0/3 -> 1/3 |
| 43991: Le Pen appeal | 3/3 -> 3/3 | 3/3 -> 3/3 |
| 44801: gasoline record | 3/3 -> 3/3 | 3/3 -> 3/3 |

All ten inputs preserve every old-visible character and satisfy the same byte
ceiling. Unique banked original characters forwarded across bundles increase
from **13,736 to 21,826**. **10,144 banked characters remain omitted** at that
ceiling, with exact ranges exported. The candidate stablecoin request now
contains the saved observation-date text, original column header, and complete
USDT/USDC rows. The request retains extracted-table document coordinates.

The two remaining candidate checks concern the Instructure incident date range
and vendor response. This tool does not certify they are irrelevant or absent.
Later rereading needs to consume the omission manifest; simply raising a saved
body count does not resolve delivery limits.

The local suite passes **501 tests**, including 12 new boundary tests. This
replay spends **zero new model/search/source requests**. It does not measure
Mercury comprehension, forecast accuracy, live recall or historical leakage.
Promotion remains disabled. Test the export on untouched material and then
verify actual analyst requests before changing competition wiring.

Reports and paired states are archived under
`E:/metaculus_data/reports/materials-handoff-37316638264/` and
`E:/metaculus_data/reports/materials-handoff-paired-37316638264.json`.
