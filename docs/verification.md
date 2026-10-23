# Verification summary

## Final hot-fix regression (local, 2026-10-23)

The current v0.2.1 candidate passed **68 Python tests with 3 optional ToolTalk skips** (71 total) and **16 Node tests**. No paid API calls were made. These totals supersede the earlier baseline counts below.

| Check | Result and meaning |
| --- | --- |
| Turn lifecycle | Single-flight spans refresh through selection; settings and selection stay frozen across model steps. A delayed old turn cannot replace a newer turn's selection. OFF bypasses the worker. |
| Refresh consistency | Approval prepares the pending new snapshot, not the old pool. Withdrawn changes clear obsolete approval. CAS checks plus a post-commit host-pool recheck permit at most one stale retry. |
| Timeout and generation | A timed-out wait retains the job; a matching retry reuses generation. The shipped worker was tested with a gated local HTTP provider, including approval, completion, cache reuse, model changes and cleanup. This is single-worker coverage, not a distributed exactly-once guarantee. |
| Progress and ETA | Elapsed time updates during generation and stops at completion. ETA uses uncached-tool completions; it remains unknown before the first completion and clears when finished. Cached hits do not inflate throughput. |
| Real DSH host | A fresh isolated profile installed the current bundle, verified its private worker, and completed **5 turns / 9 scripted chat-provider requests**. Adding a tool mid-conversation generated **one mock manual**, reused **12 cached manuals**, and selected the new tool on the next turn. Host restrictions, history and one downstream dispatch per request remained intact. |
| Distribution | Wheel, source archive and DSH bundle rebuilt; Python artifacts passed `twine check`. Archive inspection checked excluded local state and embedded-wheel identity; checksums were regenerated. |

The real-host check uses the existing DSH CLI/runtime and a complete explicitly selected Python environment. It does not exercise Desktop's GUI, visual onboarding, or a machine with no previous Toollery/development installation. Earlier bundled-Desktop-Python and empty-PATH checks below cover those specific runtime paths separately. No benchmark results were regenerated.

Remaining boundaries: Windows/pinned native root-agent coverage only; no PTC/subagent retrieval, autonomous discovery loop, same-turn model-facing expansion, or universal plugin compatibility claim. The optional discovery ranking API has worker/service coverage; an external provider's fetching, authorization and execution must be tested by that provider. Cross-process cache-lock contention fails retryably. Implementation changes with identical published tool schemas cannot invalidate a manual automatically.

## Desktop-oriented initialization (local)

Added a plugin-details setup page, independent manual model/endpoint/key form, and explicit generation confirmation in the conversation. No paid requests were made.

- Targeted Python worker checks: **3 passed**, including redacted configuration, key rotation without restart, cache-only invalidation and unsafe endpoint rejection.
- Targeted Node checks: **4 passed**, including authenticated no-session onboarding and the actual shipped worker against a local mock API. Saving settings made no provider requests; changing only the key reused the cache, while changing the model required explicit regeneration.
- With **PATH empty** and a fresh private profile, setup refused an unapproved download, then successfully downloaded and installed private **Python 3.12.12** and worker **0.2.0** after consent. No global interpreter or DSH was installed. Node fetch timed out on this environment; the implementation now uses Windows PowerShell's network stack and verifies the pinned uv SHA-256 before execution.
- The actual DSH CLI accepted the updated `.tgz` in an isolated official Web profile. A fresh isolated acceptance profile also loaded the bundle with the installed **DSH Desktop 0.12.1 bundled runtime**: the plugin reused Desktop's injected absolute resource path, created a profile-local worker with bundled Python 3.12.14, and completed four offline scripted turns without a second DSH installation. The Desktop GUI restart and visual onboarding path were not exercised; these checks do not certify a clean Desktop machine or other operating systems.

## v0.2.0 manual hot-fix acceptance (local, 2026-10-23)

The real DSH host acceptance was rerun against the rebuilt bundle with an isolated profile and an unrelated host-policy plugin. The fixture registered `uppercase_text` during an existing conversation after the initial 12-tool snapshot.

| Check | Result and meaning |
| --- | --- |
| Dynamic host registration | Passed. The next user turn discovered the new tool and selected it through Toollery. |
| Incremental manual generation | Passed. The new tool caused one provider generation call; existing valid manuals were reused. |
| Revision/selection safety | Passed. Pool revision advanced after commit, stale base revisions were rejected, and the current turn kept one frozen selection. |
| Host eligibility | Passed. A host restriction excluding `multiply_numbers` remained effective with Toollery ON and OFF. |
| OFF behavior and history | Passed. OFF exposed the current host-eligible pool, while earlier tool calls and conversation history remained intact. |

The run completed **5 turns / 9 scripted provider requests** with no paid API calls. This validates the hot-fix path against the tested DSH lifecycle; it does not certify arbitrary plugins that mutate tools after assembly, executable behavior changes with identical public schemas, or PTC/subagent agents. The fixture uses an explicit complete Python environment for the worker; Desktop's bundled-runtime setup path is covered separately above.

## v0.2.0 existing-profile plugin (local release verification)

Earlier baseline verified on Windows, 2026-10-22, using Python 3.12.7 and existing DSH `0.2.1-rc.2`. All checks below use mock manuals/scripted providers; no paid API requests or benchmark reruns were made.

| Check | Result and meaning |
| --- | --- |
| Python suite | **60 passed, 3 skipped** (63 total). Includes eligible-set retrieval, cache validation/diffs and explicit worker preparation. Optional ToolTalk tests remain skipped. |
| Existing Node suite | **9 passed**: snapshots, tool-source readiness, UI transport and launcher state. |
| Shipped bundle worker | **1 end-to-end Node test passed**: actual embedded-wheel installation and module location in a fresh private venv, conflicting Python/pip environment and profile modules, built-in provider subprocess against a local API fixture, explicit preparation, disjoint selections, host eligibility, worker failure/OFF, restart/cache reuse, version mismatch, repeated setup and disposal. |
| Official bundle installation | DSH's `plugin add` accepted the `.tgz` in a fresh isolated profile. Optional host peers passed `pnpm peers check`; the Python 3.13.4 worker contained only pip and Toollery and passed `pip check`. Its patch inserted only Toollery; the host model/persona/tool configuration was retained. |
| Desktop bundled-runtime acceptance | A separate `desktop-acceptance-20261023` home installed the `.tgz` into `sdk-minimal` and ran the real host with `DSH_DESKTOP_OFFICE_RESOURCES` pointing at Desktop 0.12.1. Bundled Python 3.12.14 created the profile-local worker (`toollery-dsh` only, as expected from `venv --without-pip`); **4 turns / 7 scripted provider requests** passed with host restrictions, history, current schemas and middleware preserved. |
| Real DSH native plugin | **4 turns / 7 scripted provider requests**. From a 12-tool snapshot, an unrelated host policy allowed 11. ON selected disjoint single tools and an empty set; OFF retained the same 11 allowed tools and executed a tool. Earlier calls stayed in history; current declarations contained only the current subset. Host prompt and downstream stream middleware were preserved, with one downstream dispatch per request. |
| Real DSH Web registration | Installed in an isolated `web` profile. The boot graph included the package, and its browser bundle served the correct module ID and setup/prepare controls. The UI route rejected unauthenticated requests (401) and admitted an authenticated request to session validation (409 for the deliberately missing session). This checks registration/transport, not browser rendering. |
| Retained standalone demo | **6 turns / 11 scripted requests** passed: original schemas, real tool execution, disjoint subsets, all/top-k, three representations, empty selection, call guard and retained history. |

The plugin uses a scoped public-stream re-entry for DSH's addition-only projection; adapter metadata can resolve twice. Dynamic adapters, earlier interceptors and arbitrary plugins are not universally covered. Native root agents are supported; PTC, subagent retrieval and Linux/macOS are outside these checks. Live pool mutation was outside this earlier baseline; the hot-fix acceptance above adds next-turn registration/refresh coverage. Browser rendering of the existing-profile UI has not been manually exercised in this round. Paid-provider retrieval/execution findings below remain evidence from the earlier standalone integration.

Build/test commands are in the root [README](../README.md); the [plugin guide](plugin.md) describes installation and lifecycle boundaries. `scripts/plugin_smoke.py` drives the real-host check against an existing Node DSH and an isolated profile, with `tests/fixtures/dsh-plugin-host.mjs` supplying an unrelated restrictive policy.

The [environment audit](environment-audit.md) records a checkout-import false positive found during dependency review and its fix. Worker acceptance now verifies the private installed distribution before exercising DSH, rather than treating a successful source import as proof of installation.

## Historical verification

For the v0.1.1 startup-hardening checks, see the [environment assumptions audit](environment-audit.md). The results below describe the published v0.1.0 verification.

## Post-v0.1.1 launcher checks (main)

- Python suite: 57 passed and 3 optional ToolTalk tests skipped (60 total); Node suite: 8 passed. Includes authenticated setup requests, credential isolation, process cleanup and workspace handoff.
- Windows bootstrap: installed private dependencies with an empty process PATH and a directory containing spaces; updated the installation to the final launcher sources successfully.
- Installed launcher, outside the source checkout: opened the setup page and authenticated DSH workspace. The scripted OFF run exposed 12/12 tools and executed `add_numbers`, returning 42. An earlier ON run selected 3/12 and returned 42; its Stop button released the demo port.
- Wheel and source archive built and passed `twine check`. These are local main-branch builds, not replacement v0.1.1 release assets.

No paid provider requests were used. Arbitrary chat, Linux/macOS, a real-model cache reused across separate launcher starts, and coexistence inside an existing DSH profile are outside these launcher checks.

## Published v0.1.0 checks

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
