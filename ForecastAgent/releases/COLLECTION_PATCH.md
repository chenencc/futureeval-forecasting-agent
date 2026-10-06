# Production collection patch candidate

Base: immutable tag `v1.0.2`, commit `163e2b9`. Candidate branch:
`fix/release-production-collection`. This does not replace the existing tag.
The production workflow remains pinned to `v1.0.1` until an explicit release
promotion updates its immutable checkout.

## Scope

- Decode terminated HTML entities after Markdown escaping. Query keys such as
  `sectionNum` survive unchanged; opaque transport URLs remain separate from
  comparison keys.
- Accept canonical equivalents of offered URL enums. Bounded read batches
  validate each child independently before reserving HTTP. Unknown URLs retain
  explicit per-item errors and never consume a fetch attempt.
- Detect plain navigation and Code Search shells, including vendor Extract
  responses whose titles are valid but bodies are menus. Short announcements
  and actual tables remain usable. Readability never establishes relevance.
- Permit one durable program capture batch before soft stall closure. It uses
  observed unread sources, remaining free HTTP capacity and existing plan
  queries. It grants no model/search calls, progress credit or new allowance.
- Include unread observed source candidates and extraction failures in the
  independent supplement inventory. Prioritize explicit rule URLs and concrete
  official details. Normalize ISO/RFC publisher dates for routing only.
- Reassess body diagnostics in a copy of the parent during handoff, retain raw
  captures and structured gaps, and exclude unreadable bodies through the
  unchanged analysis core's existing source filter.
- Record release and infrastructure commits separately.

## Unchanged contracts

The 47 frozen analysis/core files, decision prompts, score clipping, rereading
policy, comment construction and atomic submission/readback logic are unchanged.
Per task: three Tavily basic attempts, one Exa attempt, eight initial free HTTP
attempts and one existing Extract batch. The independent supplement retains two
HTTP captures, two browser renders and eight saved-byte reparses. Reservations,
failed attempts, parent hashes and accepted submission receipts remain durable.
Historical strict continues to forbid new live supplement captures.

## Verification

`test_production_collection.py` covers the public source failure fixtures from
production question 46056, mixed valid/invalid batches, unknown URLs, short
announcements, tables, unread lead capture, historical guards, resume and stall
recovery. The full offline suite also checks typed forecasts, provider failures,
100-question reliability and zero duplicate writes on replay.

`python -m ForecastAgent.releases.collection_replay` compares the same archived
production body bytes against a local `v1.0.2` checkout. Its synthetic HTTP bodies
test routing only: they do not count as new substantive evidence. The original
archive remains unchanged and no real provider or submission is invoked.

The GitHub workflow `Release collection patch gate` installs the Linux readers,
verifies source and frozen analysis hashes, runs offline regressions, and performs
at most three public HTML/PDF GET probes without model or search credentials. It
archives actual captures, failures and checksums. These current probes are not
inserted into the historical production forecast or used to revise its score.

## Promotion boundary

Successful software checks establish candidate readiness, not improved forecast
accuracy. A shell-free package may contain fewer formal analysis sources because
menus are no longer counted as evidence. Blocked sites remain recorded gaps.
Promotion should publish a new immutable patch tag, preserve the production
ledger and update the worker pin; it must not overwrite `v1.0.2`, reset provider
budgets, reopen an accepted question or resubmit question 46056.
