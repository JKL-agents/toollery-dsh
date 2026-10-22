# v0.1.0 verification summary

Verified on **Windows, 2026-10-22**, with Python 3.12.7, Node.js 24.19, pnpm 11.25 and pinned DSH `0.2.1rc2` (npm `0.2.1-rc.2`). These checks use scripted model responses and mock manuals, with no paid requests. Real-provider findings are in the [experiment summary](experiments/README.md).

## Core observations

| Check | Result |
| --- | --- |
| Python | **43 passed, 3 skipped** (46 total). Optional ToolTalk source is absent. Covers cache invalidation, retrieval, bridge authentication, OFF bypass, credentials, state and paths. |
| Node | **6 passed**: snapshot normalization, source readiness and UI state. |
| Real DSH offline chat | **6 turns / 11 scripted requests**: actual tool execution, changing subsets, retained history, three representations, all/top-k, empty selection, call guards and original schemas. |
| Official Web UI | ON selected 3/12 and returned `add_numbers = 42`; OFF exposed 12/12 and returned `celsius_to_fahrenheit = 68`. Controls and tool cards displayed in English. |
| Installed wheel | The same chat smoke passed from outside the checkout, using a fresh non-editable installation and a configuration path containing spaces. |
| Windows wrapper | A `.cmd` fixture preserved arguments, configured working directory, native-tools patch and bridge environment. A regression test covers the AutoRun issue found during verification. |
| Distribution | Wheel and source archive built and passed `twine check`. |

**Conclusion:** the packaged chat/Web paths preserve tool selection, original schemas and session history; installation does not depend on the source checkout. These are integration checks, not evidence of model quality or benchmark speed.

## Scope and reproduction

Linux/macOS and arbitrary external DSH presets remain unverified. The external-command wrapper has launch-level coverage; GitHub CI has not yet run remotely. Skipped tests are not counted as passes.

Use the root [README](../README.md) for installation, offline chat/Web checks and development test commands. The [publishing checklist](publishing.md) covers artifact validation. Run installed-wheel checks from a separate directory to verify configuration-relative resources and state.
