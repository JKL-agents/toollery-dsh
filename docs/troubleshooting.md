# Troubleshooting

For the **existing-profile plugin**, start with `/toollery status` or the Web controls. For the **standalone demo**, use `toollery-dsh doctor --config path/to/toollery.json`; add `--require-web` for Web. Doctor makes no model calls and reports credential presence without values.

## Existing-profile plugin

| Symptom | Action |
| --- | --- |
| DSH install/reinstall reports a missing npm metadata or cache blob | This can be a local package-manager cache inconsistency rather than an invalid Toollery package. If the message names npm, close DSH, run `npm cache clean --force` in PowerShell, reopen DSH, and retry. This clears npm's cache, not Toollery's API settings or manual cache. If DSH still reports a cache error, install the current release `.tgz` and include the exact message when reporting it. |
| No Toollery controls/command | Confirm the bundle is enabled in this profile, restart DSH and start a new native conversation. Install the built release `.tgz`, not the raw `plugin/` source directory. |
| `Worker setup failed` immediately after upgrading from 0.2.1 | A 0.2.2 Desktop upgrade could retain old Toollery distribution metadata in the profile-local pip-free venv. Upgrade to a release containing the worker-upgrade fix and retry **Set up local worker**. Setup is offline and does not request an API key; the key is used only for later manual generation. Existing configuration and manuals are retained. |
| `needs_setup` | Use **Set up local worker** or `/toollery setup`. Python 3.12+ with venv/pip must be available; set `TOOLLERY_PYTHON` to an absolute executable path if discovery fails. |
| `needs_preparation` | Edit `manual-generator.json` and `.env` in the directory shown by status, then select **Generate missing manuals (uses API)** or `/toollery prepare`. This permits generation charges. |
| Credentials/configuration changed | Saving the plugin form rechecks readiness without restart or generation. Key-only changes reuse manuals; model/endpoint changes may require regeneration. Restart after manual file edits. DSH's chat credentials are separate. |
| Worker stopped or ON fails | Switch OFF to use the host's allowed tools without retrieval. Run setup/restart to restore the worker. No silent all-tools fallback occurs while ON. |
| Unsupported PTC/no-tool agent | Use a native root conversation with tools already registered. Subagents retain ordinary DSH behavior. |
| Tool pool changed | The next user turn reconciles additions, changes and removals. Valid matching manuals are reused; a small cache miss may refresh automatically when allowed. Larger refreshes report approval required; use preparation or start a new conversation if the host itself has not refreshed its visible pool. |
| `approval_required` | Approve in the panel or run `/toollery prepare`, wait until ready, then resubmit the original message. Approval targets the pending new pool. The failed turn is not automatically replayed. |
| `refresh_pending` | The default 120-second wait ended; generation may still be running. Inspect progress/ETA, wait, and resubmit to reuse the job. Do not assume timeout cancelled provider charges. Set the plugin option `refreshTimeoutMs` for a longer wait. |
| `refresh_failed` / cache lock conflict | Check provider settings and retry preparation. Another process holding the same cache identity produces a retryable failure; wait for it to finish and retry. Remove a stale lock only after its owner has stopped. |
| ETA shows estimating | An uncached-tool completion is needed to estimate remaining time. Cache hits do not count toward this rate; small batches may finish before an estimate appears. |
| `unstable_tool_pool` | The pool changed during both permitted refresh attempts. Wait for sources to stabilize, then resubmit. |
| Setup was interrupted | Confirm setup/DSH has stopped, remove only `setup.lock` inside the shown configuration directory, and retry. |

See the [plugin guide](plugin.md) for installation, upgrade, removal and the version-pinned tool-history compatibility boundary.

## Standalone demo

| Symptom | Action |
| --- | --- |
| Command not found | Use the environment's `Scripts/toollery-dsh.exe` on Windows or activate that environment. |
| Node missing or incompatible | Install Node 22.19+ on 22.x, or 24+; reopen the terminal and run `node --version` there. Do not rely on an IDE's private runtime/PATH. Node 23 is unsupported. |
| pnpm missing or incompatible | `setup-web` requires pnpm 11.x. Check `pnpm --version` in the same terminal. pnpm is optional after runtime installation. |
| Cannot read configuration | Pass `--config`. `init` preserves existing files; compare templates when upgrading. |
| Missing credentials | Put `.env` next to the config. Use the separate key names or shared `TOOLLERY_API_KEY`. |
| Missing manuals / snapshot HTTP 503 | Run `prepare` using the same config/interpreter/generator. Inspect `manual-preparation.json` in the report directory. |
| Changed tools fail cache-only startup | Run `prepare` when the cache is intentionally offline. Runtime reconciliation never silently falls back to spec-only retrieval. |
| Generation rejected | Check model/endpoint and failure types. Exactly three valid query/thought entries are required; partial manual pools are not accepted. |
| Interrupted generation leaves a lock | Stop processes using the cache, then remove only that stale `.lock`. Locks prevent duplicate paid calls. |
| Web/MCP runtime missing | Install Node/pnpm and run `setup-web`. It uses the shipped lockfile, not global DSH packages. |
| Port busy | Stop the old server or use `--port 3081`. |
| Web opens but rejects access | Copy the complete authenticated `dsh web:` URL from the running terminal, including `?token=...`. The bare localhost URL does not authenticate a new browser. |
| Custom generator or host works only from one directory | Pass an absolute `--config`. Commands run from that config's directory; use explicit executable paths or install commands on PATH. |
| MCP fails before discovery | Check its executable, configured `cwd`, referenced environment variables and pinned runtime (`setup-web`), then restart. `doctor` checks local prerequisites without contacting servers. |
| No controls yet | Select a workspace and create/open a session; controls wait for discovery/preparation. |
| ON fails after OFF startup | Run `prepare` and restart, or explicitly permit generation at launch. |
| No matching tool / few candidates | Top-k omits zero-score tools. Restate the capability, increase k, use all or OFF. |
| Follow-up misses tools | Latest-user-text retrieval is a known limitation. Restate the task or broaden access. |
| Tool changed during session | Restart; live registry mutation is rejected. |
| Process/network denied | Check sandbox/firewall policy. DSH needs child processes and localhost HTTP; setup needs registry access. |

`chat --offline-smoke` uses real DSH/tools with a scripted model; `web --offline` supports plumbing inspection. Neither measures model quality. Mock and real manual caches are separate.

Reports can include prompts, schemas, results and responses. Keep raw reports private; redact before sharing. Include versions, command, doctor output and error type in issues, never credentials. Error summaries avoid echoing arbitrary provider content.
