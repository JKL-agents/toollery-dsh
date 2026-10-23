# Toollery for DeepSeek Harness v0.2.0

**Released 2026-10-23.** Install Toollery into an existing DSH profile. The standalone Windows demo remains available; the retrieval algorithm and earlier benchmark conclusions are unchanged.

## Install the plugin

In **DSH Desktop 0.12.1** (DSH runtime **0.2.1-rc.2**), open **Plugins → Add plugin → Install a third-party plugin** and enter the `.tgz` asset URL or downloaded file's absolute path. Enable and restart if requested. Desktop supplies its Node/package manager; no second DSH installation is needed.

Open Toollery's details, set up the local worker, and save your manual-generation model, OpenAI-compatible endpoint and key in the form. Windows x64 users without Python can explicitly allow a private Python download; other platforms require Python 3.12+ with venv/pip. Setup and saving settings make no model calls.

Start a new native conversation, review the pool and missing-manual count, confirm API charges, and generate manuals. Valid caches are reused. DSH keeps its chat model and credentials; Toollery does not copy them. See the [plugin guide](plugin.md), including the current Desktop verification boundary in [verification](verification.md).

CLI alternative (requires its own Node/pnpm/Python environment):

```powershell
dsh plugin --profile web add C:/absolute/path/jkl-agents-dsh-toollery-0.2.0.tgz
dsh --profile web
```

Replace `web` with your CLI profile, not Desktop's application-owned profile. `/toollery setup` and `/toollery prepare` remain available; use the UI for runtime-download consent and model configuration. The key is stored in a local profile file, not an OS keychain, and is never returned to the form.

## Changes

- Official-format DSH bundle with profile-local worker, cache and configuration.
- Existing host models, personas, providers and tool registrations are preserved; retrieval operates within the tools the host allows.
- Web controls and `/toollery` commands for setup, preparation and next-turn settings.
- Current-turn schemas replace earlier subsets on DSH's addition-only model projection, while conversation history stays intact.
- Added per-turn pool reconciliation and bounded manual hot refresh: additions and schema changes are detected on the next user turn, valid manuals are reused, and small cache misses can be generated once without restarting DSH. Refreshes use revision checks and never overwrite a newer host snapshot.
- Added an optional provider-owned discovery hook: providers may submit a remote candidate batch for local manual/BM25 ranking. Toollery persists candidate schemas and manuals, returns snapshot-bound IDs, opaque provider references and schema hashes, and leaves remote fetching, authorization, executor mapping, registration and execution to the provider. This is an API, not autonomous agent discovery.
- Manual preparation/refresh shows progress, elapsed time and an approximate ETA. Refresh waits default to 120 seconds; timeout preserves background generation for reuse. Above-policy updates require approval and explicit message resubmission.
- OFF works even when the worker is unavailable. Unloading cleans up plugin-owned resources and retains local configuration/cache.
- Shared retrieval/UI code with the standalone demo. Python SDK/runtime dependencies move to the optional `[demo]` extra; manual standalone installations must install `.[demo]` or the wheel with `[demo]`.
- The standalone demo waiting page now shows startup stages, elapsed time and failure feedback.

## Scope

Verified locally on Windows with pinned DSH runtime 0.2.1-rc.2, including Desktop 0.12.1 bundled Python reuse in an isolated profile. Desktop GUI onboarding remains unverified. Native root conversations are supported; PTC and subagent retrieval are outside this version's scope. Settings/selection are fixed per turn; registered pool changes reconcile on a later turn without restart. Retrieval still uses the latest user text, without history rewriting or recovery search.

Targeted offline checks cover official bundle installation, embedded-worker setup, cache reuse, host tool restrictions, changing subsets, retained history, dynamic tool registration with hot refresh, and the retained standalone demo. This is bounded integration coverage, not a claim of compatibility with every plugin. See [verification](verification.md) for results.

For the standalone double-click demo, download **Source code (zip)** and run `Start-Toollery.cmd`; the `.tgz` is for an existing DSH installation. Distribution remains GitHub-only, with no npm/PyPI publication. Existing releases are unchanged.
