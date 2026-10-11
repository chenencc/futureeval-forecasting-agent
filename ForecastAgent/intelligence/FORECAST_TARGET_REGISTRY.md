# Experimental forecast target registry

The acquisition plan contains research needs. Completing a schedule lookup,
finding a definition or processing all delivered excerpts does not establish
coverage of the eventual resolving outcome.

Enable the separate target explicitly with `research_map.enable(...,
forecast_target=True)`. This adds `forecast_target_registry_policy` with value
`question_and_research_targets_v1`. Production defaults remain unchanged.

The registry preserves existing `T` research-need identifiers and adds one `F`
forecast target. Its identifier hashes the exact question identity, type,
resolution criteria, fine print, platform scaling and options. Changing a
research plan does not change the forecast target. Changing the authoritative
resolution contract does. Both targets remain fallible annotations, not facts.

Observations can link to a research need and the forecast target separately.
Current measurements, guidance and market prices are labeled baseline or
indicator, with their scope and limitations. An unpublished realization stays
unknown. A research-need link never implicitly creates a forecast-target link.
An absent forecast link is reported as unassessed, not covered or false.

This changes the opt-in task contract. Freeze it before acquisition. Existing
snapshots cannot silently adopt it: use a separately audited derivative or a
new preregistered trial, retaining original materials and cumulative budgets.
No new tool, provider allowance, probability computation or truth certification
is granted by the registry.

Offline evidence on the two 2026-10-11 saved graphs confirms that existing
research-need coverage does not cover the independent forecast target. Models
have not yet built graphs against the new registry. Predictive improvement and
production readiness are unproven. The prior sports trial delivered the 42.5
market total and team scoring baselines to the model; their omission was not a
body-delivery failure. The prior legal graph retained an interpretation error
despite valid literal references. The registry does not solve either semantic
problem by itself.
