# Local development credentials

Run the interactive setup in Windows PowerShell:

```powershell
& .\ForecastAgent\integrations\configure_local_keys_gui.ps1 -ReconfigureAll
```

The GUI requests the Actions secrets one at a time: `OPENROUTER`,
`OPENROUTER2`, `TAVILY_KEY1`, `TAVILY_KEY2`, `EXA_API`, `EXA_API2` and `METACULUS_TOKEN`.
Input is masked and each saved entry is encrypted immediately. `-ReconfigureAll`
prompts again for existing entries; cancel preserves previous successful saves.
GitHub Secret values cannot be retrieved through GitHub CLI. Supply keys
from your own provider accounts, never through chat or command-line arguments.
The console setup remains available with `configure_local_keys.ps1`.

Configure only the optional Exa backup without re-entering other credentials:

```powershell
& .\ForecastAgent\integrations\configure_local_keys_gui.ps1 -ReconfigureAll -OnlySecrets EXA_API2
```

Values are saved as Windows DPAPI-encrypted SecureString values in
`.local/credentials.clixml`. The directory is Git-ignored and its ACL permits
the current Windows account and SYSTEM. Decryption requires the same account
and computer. Do not publish or include this directory in artifacts.

To see configured names without exposing values:

```powershell
& .\ForecastAgent\integrations\configure_local_keys.ps1 -StatusOnly
```

To load credentials before invoking a Python module:

```powershell
. .\ForecastAgent\integrations\load_local_env.ps1
# Alternatively select the backup keys explicitly:
# . .\ForecastAgent\integrations\load_local_env.ps1 -OpenRouterSecret OPENROUTER2 -TavilySecret TAVILY_KEY2
python -m venv .local/venv
& .local/venv/Scripts/python.exe -m pip install -r ForecastAgent/requirements-supplement.txt
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '.local/browsers'
& .local/venv/Scripts/python.exe -m playwright install chromium
# Invoke the chosen collection or analysis module with its frozen input,
# output directory, model settings and preserved continuation ledger.
```

Loading credentials does not invoke providers, submit forecasts or change model
routing. OpenRouter and Tavily backups require explicit selection. Exa switches
automatically from `EXA_API` to optional `EXA_API2` only for documented HTTP 402
credit or spending-budget exhaustion tags. Ordinary rate limits, authentication,
request, payment-protocol and service errors do not switch keys. Switching never
resets task budgets. Runtime environment names are `OPENROUTER_API_KEY`, `TAVILY_API_KEY`,
`EXA_API_KEY`, optional `EXA_API_KEY2` and `METACULUS_TOKEN`. Only this process and its child processes
receive decrypted values. Provider calls still consume account quotas. A local
execution must preserve the experiment manifest, source hashes, continuation
identity and cumulative budgets; changing execution host never resets them.

The loader installs an environment-gated Python startup hook in `.local/venv`.
It is inactive without `FORECAST_EXA_FAILOVER_MODULE`, and survives child workers
that replace `PYTHONPATH`. Start a new local process to pick up newly saved keys;
running frozen experiments keep their original environment. Transport receipts
are stored in `.local/exa-transport/attempts` with key roles, payload hashes and
HTTP status only. Usage is marked unknown when not reported. A failed primary
and its backup are two physical attempts under one existing logical Exa search.
The exhausted route persists for the current UTC calendar month and primary-key
digest; the next month or primary-key rotation tries the primary again. This
does not assert that the provider actually resets credits monthly.

The setup script does not configure optional `SEC_USER_AGENT`. If SEC collection
is needed, supply a descriptive contact user agent in the runtime environment.

## Local debug launcher

Use the isolated environment with the same repository modules as Actions:

```powershell
& .\ForecastAgent\integrations\run_local.ps1 -Module ForecastAgent -PythonArguments @('--help')
& .\ForecastAgent\integrations\run_local.ps1 -Module ForecastAgent -PythonArguments @('channels')
# Select an isolated development worktree while reusing the main encrypted store:
# & .\ForecastAgent\integrations\run_local.ps1 -Workspace D:\metaculus\.tmp\dev_forecast_model -Module ForecastAgent.forecast_model -PythonArguments @('--help')
# Pass the exact frozen manifest and original continuation paths for an experiment.
# Choose backups explicitly with -OpenRouterSecret OPENROUTER2 -TavilySecret TAVILY_KEY2.
```

The launcher loads encrypted credentials only for the current invocation, runs
from the repository root, and restores the previous process environment afterward.
It uses `.local/browsers` for Chromium and does not dispatch Actions or reset
experiment budgets. The chosen module determines whether provider calls occur;
`--help` and the channel catalog are safe startup checks. Run experiments in
isolated output directories. Production scheduling and submission remain separate.
