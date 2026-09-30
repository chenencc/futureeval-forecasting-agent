---
name: economic-data
description: Research financial prices and statistics with dated, versioned evidence.
---

Identify ticker or series ID, units, currency, timezone, close versus intraday, adjusted versus raw values and interval quantifiers. Prefer the designated data provider; Yahoo is not interchangeable with Treasury data. Read real catalog URLs with fetch_pages: Yahoo and ALFRED adapters are automatic. ALFRED live CSV vintage retrieval was verified; report failures without silently replacing vintage data. Require an exact dated series column (ISO or compact date) and exclude missing observations. Observation period is not publication time. First-release questions require release-vintage evidence. Use news to explain drivers after obtaining eligible observations. Never fetch future outcomes to fill a historical gap.
