# Domain routes

Query `intelligence_catalog` for current supported parameters. These routes
explain source selection, not predictive confidence or a required sequence.

## Finance and company earnings

- `sec_submissions`: identify an issuer's exact filing/accession.
- `sec_concept`: exact CIK, taxonomy and concept; inspect unit, start/end, filed,
  form and accession. Quarterly and cumulative facts differ.
- `tesla_ir` / `microsoft_ir` profiles: issuer originals and non-GAAP disclosures.
  Use `intelligence_discover(kind="ir", profile_id=...)` for document leads;
  retain entity, fiscal period and report version. Read exact table rows offline.
- `bls_series`: no-key single exact series, provider default three-year window.
  Keep string values, M13 annual periods and footnotes; units/seasonal adjustment
  require series documentation. v1 no-key quota is 25 queries/day per provider/IP;
  the task cap does not replace that shared limit.
- `dbnomics_series`: one exact provider/dataset/series with native missing values
  and dimensions. Freeze explicit dataset releases; `latest` is rejected.
- `worldbank`, `fred_csv`: public numeric windows. Neither current CSV nor
  observation year is an as-of historical publication vintage.
- `fed_news`: announcement feed, then select official original document links.

## Politics, elections and laws

- `congress_bill`, `congress_actions`, `congress_texts`: exact US bill identity and
  versioned text. Host loads the existing Congress key. Original public-law
  selection requires matching detail evidence; see Invocation.
- `govinfo_feed`: collection RSS leads; use lowercase official RSS URLs in discover.
  `govinfo_text` gets an exact package HTML rendition with no API key. Prefer
  `original_rendition` candidates over `/app/details/` metadata pages. A package
  may lack HTML; select an explicitly saved alternative PDF/XML instead.
- `federal_register` / `federal_register_detail`: exact notice/rule and associated
  dates. A proposed rule is not enacted law; inspect official rendition and stage.
- `uk_bills`, `uk_bill_detail`, `uk_bill_publications`: staged bill/publication data.
- `uk_legislation_xml`: exact statute/version and original provision IDs.
  The latest revised text is not necessarily as-enacted or as-of text.
- California/Brazil election profiles: official calendars, announcements and
  detailed results leads. Preserve office, geography, round and tally/certification
  stage. FEC directory identifies authorities; it is not live state certification.

## News, health, environment and science

- `gdelt_news`: entity/phrase/domain-filtered news leads. Explicit UTC start/end
  bounds replace the relative timespan. Indexing date is not proven publication
  date. Saturated top-N output is not complete coverage. Keep at least five seconds
  between DOC queries; explicit retries still cost requests, and shared egress can
  cause 429s. Review article hosts before passing them to the host's allowlist.
- `clinical_trials`: sponsor-reported trial registry, not independently verified
  efficacy or approval evidence. Preserve exact study and registry fields.
- `usgs`: timestamped/geographic earthquake observations with revised magnitude.
- `nasa_eonet`: natural-event aggregation; retain original source links and dates.
- `crossref`: paper metadata/available abstracts, not full papers or confirmed claims.

## What this toolbox does not yet provide

CourtListener authentication, ALFRED vintage API, arbitrary-range BLS POST,
a global provider rate limiter, Office parsing, OCR, automatic Playwright fallback
and automatic article-host expansion are not implemented here. Existing native
ForecastAgent browser/search tools are separate capabilities; using this skill
must not grant extra Tavily/Exa or campaign allowances.
