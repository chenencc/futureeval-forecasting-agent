# Matched original coverage experiment

Branch: `dev_formal`. Production remains pinned to `v1.0.1`.

For each of the frozen forty cases, load the historical final successful Mercury request: second stage if a saved second response exists, otherwise first stage. Copy its state unchanged. Both new stages retain exactly its question, evidence text, offsets, IDs, source metadata and order. No new source selection, balancing, resegmentation, capture or supplement occurs.

Stage one asks five independent condition questions with explicitly referenced passage choices. Stage two adds uncertain condition bindings to that same state and uses the historical event question with an explicit instruction to consider those bindings. Candidate choice descriptions are compacted to avoid repeating instructions. Full distributions are saved; candidate-selection probabilities are not independent condition-truth probabilities.

The program checks every original quotation and body hash before inference and checks coverage hashes before final scoring. Each case permits two physical attempts under frozen request journals. No historical labels or previous event probabilities are supplied. Distribution grids and clipping are unchanged. Date grids without platform metadata remain research-only.

The 96,000-byte transport ceiling is not a token budget or a guarantee of fitting the provider context. The provider enforces its token limit. Oversized input fails explicitly; evidence is never silently trimmed. Preflight checks transport size and original provenance without provider calls.

Compare against historical baseline metrics and the earlier reduced-coverage condition experiment. Equal source content controls the original-text coverage difference, but historical versus fresh inference is not a simultaneous randomized control. Differences in instructions, diagnostic outputs and input representation remain part of the experimental treatment. Do not attribute all score differences to reasoning decomposition alone.
