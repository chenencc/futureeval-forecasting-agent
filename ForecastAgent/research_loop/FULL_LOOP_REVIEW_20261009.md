# Native acquisition and map loop: three-case review

## Scope and implementation

The experimental branch is `codex/research-agent-fusion-v1`. The final runtime
candidate is `d03b837dcfa280ad5ceca7e22c25f6c189505568`. This local trial uses
Super directly. Mercury scoring, forecast submission and production changes are
disabled. It exercises the complete native acquisition runtime rather than
stopping after the first accepted map.

The opt-in repairs expose the raw URL capture schema, allow a bounded local
inspect/update cycle after material changes, explicitly budget hidden reasoning,
avoid delivering duplicate inspected evidence, and support audited migration of
interrupted state. Exact original text, failed tool calls, response records and
the first-start clock remain preserved. The native context ceiling remains
28,000 characters.

## Results

| Question | Super HTTP attempts | Reported tokens | Tavily basic | Exa | Accepted map revisions | Native stop |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 46074: WNV country count | 11 | 114,499 | 1 | 1 | 1 | Stalled; closed with gaps |
| 46078: OpenAI announcement | 6 | 61,014 | 0 | 1 | 0 | Context projection failure; incomplete state preserved |
| 46124: ALCS after Game 4 | 11 | 134,528 | 2 | 1 | 1 | Stalled; closed with gaps |

The final ledgers contain 28 model HTTP attempts and 310,041 known tokens.
All responses contain choices. Initial smoke trials add eight physical requests
and 80,071 tokens. After deduplicating inherited original receipt hashes across
all experiment phases, the total is 36 requests and 390,112 tokens. Model-reported
cost is zero; this does not describe search-provider billing. Each final case
respects the three-Tavily, one-Exa and unchanged cumulative collection caps.

There were five network actions after an initial map, three linked to declared
research nodes, and six newly readable post-map body events. **None of those
bodies was bound into a later accepted map revision.** The two initial maps have
four observation nodes and no relations. Successful initial binding and exports
do not demonstrate a complete acquisition/map feedback loop.

## Original-material review

- **WNV:** Two dashboard versions contain useful dated country-count baselines.
  The `index.html` capture reports 16 countries with data through September 30,
  published October 2. This is not the future mid-October resolution value.
  About 64,478 captured characters are inline image encoding. Three thin landing
  pages also pass the generic `usable_text` diagnostic. After follow-up capture,
  three map replacements fail because removed old nodes are not explicitly
  retired. Useful material is preserved but not absorbed into a later map.
- **OpenAI:** The three saved forum JSON responses total 158,795 characters.
  Decoding their saved post bodies yields about 14,890 visible text characters,
  plus timestamped posts and official announcement links. The trial did not use
  that decoded view. The rule-named news and release-note pages are not captured.
  Offline replay reproduces a 30,070-character context against the 28,000-character
  ceiling: system instructions alone occupy 11,418 encoded characters. The error
  message mentions instructions but the complete protected message group and
  state also contribute. This is a delivery failure without a model request.
- **ALCS:** The secondary article has an NLCS title but contains exact ALCS team
  and schedule passages. Both accepted observations have contiguous literal
  support; rejecting the page solely by its title would be incorrect. Three
  official pages are later rescued by basic Extract, but do not enter a new map.
  Unavailable-tool selection, undeclared capture parameters and a failed read
  lead to the native stall stop.

Future unpublished results remain expected unknowns. The review evaluates saved
materials and engineering behavior; it does not verify outcomes or estimate
forecast accuracy. The independent release supplement runs outside the map
agent's feedback loop and is reported separately.

## Verification and next gate

99 related offline tests pass. Original question hashes, original model response
hashes, archived candidate source hashes and continued WNV ledger prefixes match.
The WNV continuation preserves its original clock, search allowances and model
allowances. Already closed smoke tasks are not reopened.

The experiment is **not ready to expand or promote**. Prioritize structured
material normalization with bounded evidence-window delivery, actionable map
delta/retirement errors, and consistent program action guidance versus filtered
tool schemas. Replay the saved failure states before another live search trial.
Keep this as one acquisition/map loop; splitting it into unrelated stages would
not verify the intended behavior.

English structured reports, original records, all interrupted/closed states and
candidate code archives are retained with SHA-256 verification in the persistent
experiment archive. Earlier saved-body map tests are interface references rather
than full-loop controls; this three-domain trial is exploratory.
