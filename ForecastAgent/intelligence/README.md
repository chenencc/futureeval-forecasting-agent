# Predictive intelligence development candidate

This local candidate is maintained on `codex/official-intelligence-v106`, starting
from the frozen `v1.0.5-crawl4ai.1` code (`ba94cea`). It is not a production release.
Existing release files, workflows and submission behavior are unchanged.

## Four capabilities

1. **Body admission and deduplication.** Build a derivative view that excludes
   extraction shells, title-only responses and discovery snippets. Normalize
   whitespace for content deduplication and prefer the rule-designated URL when
   identical bodies have multiple addresses. Keep original captures, hashes,
   aliases, timestamps and character coordinates. Readability does not certify
   relevance or truth. A short dated announcement can remain valid evidence.
2. **Predictive information contract.** Preserve exact platform rules and scales.
   Distinguish definitions, current baselines, comparable history, drivers and
   future outcomes. Only an explicitly declared future observation with a later
   availability time can be `expected_unknown`. Undeclared legacy requirements
   remain `needs_observability_declaration`; their meaning is not guessed from
   names such as `weight_2026`. A timezone is mandatory. This clock is not a
   guarantee against outcome leakage in present-day web pages or model memory.
3. **Observed data-resource recovery.** Rank exact rule links and saved links to
   CSV, JSON, spreadsheets, PDF or API resources. Decode Markdown query escaping
   without corrupting parameter names. Read using the existing safe HTTP reader.
   Reserve attempts durably before I/O and share the prior independent supplement
   HTTP cap (normally two), including failures and unfinished reservations. No
   additional Tavily, Exa, Extract, model or browser allowance is created. Missing
   budget ledgers block this extension. Exhaustion retains explicit candidates
   and gaps; it does not manufacture evidence or a forecast.
4. **Optional grounded forecast brief.** In a bounded paired experiment, freeze
   identical original spans for direct Mercury and one short Super brief followed
   by Mercury. The brief separates quoted observations from unverified factors
   and uncertainties and contains no probabilities. Quarantine non-verbatim
   quotes without rewriting them; retain valid observations and raw provider
   responses. Exact quotation binding does not prove claim entailment. Each arm
   gets one Mercury HTTP attempt and the brief gets one Super HTTP attempt:
   three lifetime attempts per case. This brief is experimental, not a default
   replacement for the release analysis core.

## Connected acquisition entry point

```python
from ForecastAgent.intelligence.pipeline import collect

result = collect(request, output_directory, clock_utc="2026-10-10T00:00:00Z")
```

The entry point passes the predictive contract as task metadata to the existing
v1.0.5 collector. It runs the existing collector and independent supplement,
builds admission diagnostics, extends data recovery using any remaining
supplement HTTP slots, and exports a new admitted analysis view. It never submits
or starts analysis. `recover_data=False` disables the data extension.

The legacy agent planning schema remains frozen. Its natural-language use of the
new contract is advisory and has not yet been verified in a fresh collection
pilot. The program reports undeclared legacy requirement types rather than
pretending they were repaired. The existing collector is not retroactively
changed by a snapshot replay.

## Official data adapter

Observed `clinicaltrials.gov/study/NCT...` identifiers can be read through the
documented `https://clinicaltrials.gov/api/v2/studies/NCT...` route.
[The US NLM explanation](https://www.nlm.nih.gov/pubs/techbull/ja25/ja25_clinical_trials_screen-scraping.html)
describes why API access is preferable to scraping the JavaScript website.
The adapter checks that the response identifies the requested study and retains
actual status, update date and intervention names. One study does not establish
an exact intervention match or an exhaustive population count. Search filters,
pagination and completeness need a separate count adapter before this can
resolve a population-count gap.

## Immutable snapshot replay

```powershell
python -m ForecastAgent.intelligence.pipeline --audit <audit.json> --archives <archive-root> --output <fresh-output>
```

Default replay has no network calls. `--model-ids` explicitly permits at most
three cases and nine total model attempts. Credentials come from the environment;
do not put them in command arguments or reports. Changed inputs, code identities,
clocks or allowances refuse a silent resume. A compatible completed capture or
brief is reused. A valid direct decision survives failure of the brief arm.

Artifacts include `requirements.json`, `admission.json`,
`source-recovery-plan.json`, `analysis-view.json`, exact shared spans and provider
journals. Data repairs are separate timestamped captures and overlays; original
collection files and quota ledgers are preserved.

## Validation and limits

- Nine archived production cases: 35 saved bodies, 24 admitted distinct bodies,
  10 exclusions and one duplicate. Fresh v1.0.5 diagnostics already exclude two
  of the ten; eight exclusions are additional. Useful short event and vote
  announcements survive. One all-shell clinical-trial packet needs recovery.
- Binary, multiple-choice and numeric decision pilot: all three paired outputs
  completed, using nine actual model HTTP records. One invalid quotation was
  quarantined by reprocessing its saved response; no extra Super call was made.
- The approval case moves from about 46.9% to 88.1% after the brief. Its original
  material emphasizes net approval and lacks the target absolute-approval
  baseline/history. This is an amplification risk, not evidence of improvement.
- Tests cover frozen release integrity, preservation, exact coordinates,
  shared-cap exhaustion, interruption, cached results and official-study identity.
  A single public-service probe also retrieved readable official JSON and checked
  the documented example study identity. It was saved separately from question
  packets. Tests do not prove live agent adoption, new-source recall or Linux service
  availability. The production cases are unresolved in the audited snapshot,
  so no accuracy or Brier improvement is claimed.
- The content novelty helper is available for later reread integration; this
  three-request experiment does not implement a conditional second Mercury read.

Before promotion, use a small fresh collection pilot with the exact same baseline
and budgets, inspect actual target-field coverage, and keep future outcome labels
separate. The next gate concerns real acquisition coverage, not increasing
confidence or simply adding more calls.
