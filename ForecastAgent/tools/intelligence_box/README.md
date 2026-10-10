# Extracted intelligence source and navigation core

Python modules are imported unchanged from Channels commit `dde8b02`. They retain
the standalone source/parser contracts for eighteen API/feed sources and five
curated profiles. Original channel validation and onboarding assets remain in
the Channels worktree; they are not production integration evidence.

Use [the native channel adapter](../../channels/README.md) inside ForecastAgent.
It binds the core to native budgets, source discovery, immutable captures,
reference projection and research feedback. The independent Toolbox CLI is only
for explicitly separate bounded experiments; its standalone cap is never a
worker allowance. Saved-reader output does not establish relevance or truth.
