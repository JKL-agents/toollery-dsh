# Changelog

## 0.2.0 - 2026-10-23

- Add an official-format DSH bundle for existing profiles, with a plugin-owned Python worker and shared retrieval core.
- Preserve the host's providers, model settings, personas and tool registrations; retrieve within the host-allowed set.
- Add explicit worker setup and manual preparation with status/progress, independent manual credentials and profile-local caches.
- Reconcile registered pool changes on later turns with cache-miss-based generation limits, revision checks, bounded stale retries and frozen per-turn settings/selection.
- Bind generation approval to the pending new snapshot and require explicit message resubmission; retain timed-out jobs for reuse instead of repeating paid calls.
- Show manual-generation elapsed time and approximate ETA; use a configurable 120-second refresh wait.
- Add an optional provider-owned discovery ranking API with snapshot-bound IDs, opaque executor references and shared manuals, without changing host registration or execution permissions.
- Add a plugin-details initialization form for manual model/endpoint/key, explicit generation consent, and optional private Python setup on Windows x64 without preinstalled Python.
- Preserve historical calls while projecting current-turn declarations on DSH's addition-only models.
- Make OFF usable without a worker; clean up plugin-owned resources on unload.
- Keep the standalone launcher/demo. Move SDK/runtime dependencies to the Python `demo` extra.
- Replace the static demo waiting tab with startup stages, an animated progress bar, elapsed time, safe package counters and failure feedback.

## 0.1.2

- Add Windows x64 double-click installation with private Python, Node.js and pnpm; no preconfigured PATH required.
- Add a local browser setup page for tool pools, retrieval settings, independent model credentials, explicit manual generation and a free scripted preview.
- Open an authenticated DSH demo workspace, retain manuals across restarts, choose a free port when busy, and stop the owned process tree.
- Reorganize the README around a no-key preview, real-model setup, restart instructions and optional manual installation.

## 0.1.1

- Add DSH Web UI recordings showing per-turn k changes and the in-conversation OFF switch.
- Fix a Windows CI assertion for canonicalized short paths and update checkout/setup actions to Node.js 24.
- Check Node/pnpm versions with bounded probes and actionable terminal/PATH diagnostics.
- Resolve custom generator and host commands from the configuration directory; handle Windows npm shims consistently.
- Validate Web runtime metadata/entry, busy ports, MCP executables/directories and referenced credentials before startup.
- Make pnpm a setup-only requirement in doctor; check generator configuration without assuming custom generators need API credentials.
- Clarify prerequisites and the authenticated Web URL in user guides.

## 0.1.0

- Independent Toollery preparation, validated three-query manuals, persistent caches and BM25.
- Cordis integration with complete-pool discovery, fixed per-turn subsets, original schema projection and guards.
- Installable initialization, doctor, preparation, CLI, Web and advanced host-bridge commands.
- Separate chat/manual configuration, environment credentials and config-relative state.
- English Web controls and turn cards; demo, filesystem MCP and configurable sources.
- ToolRet subset retrieval evidence and ToolTalk OFF/ON execution limitations.
- MIT original code and explicit method, prompt and dependency acknowledgements.
