# Toollery for DSH

An independently written DSH plugin implementing [Toollery](https://arxiv.org/abs/2609.22218). MIT-licensed, by Jikun Liu. Not affiliated with DeepSeek or the original Toollery authors. See the [original project](https://github.com/XiangxiTian/toollery).

## DSH Desktop

The local target is **DSH Desktop 0.12.1** with DSH runtime **0.2.1-rc.2**, a working chat model and native tool presentation (Standard preset). Isolated host checks passed. The maintainer and a colleague have also reported successful Desktop installation; the colleague's exact environment and feature coverage were not recorded.

1. Open **Plugins → Add plugin → Install a third-party plugin**. Enter `@jkl-agents/dsh-toollery` to install the current npm release, or use a release `.tgz` URL/downloaded bundle's **absolute local path**. Install, enable, and restart if requested. Desktop supplies its own Node/package manager.
2. On startup, Toollery automatically checks the worker. When DSH's bundled Python or an existing Python 3.12+ is available, it performs the safe local setup offline. If no usable Python is available, open Toollery's plugin details and select **Set up local worker**; Windows x64 users without a usable bundled/system Python can allow a private runtime download. It does not install another DSH/Python runtime or modify PATH, and setup makes no model calls.
3. For tool retrieval (or `both`), save your manual-generation model, OpenAI-compatible API base URL and API key in the plugin form. DSH retains its own chat configuration. Skills-only retrieval does not need tool manuals or these credentials. The key stays in a local profile file, not an OS keychain; it is not returned to the form. Blank retains an existing key.
4. **For tool retrieval or `both`:** start a new native conversation. Review its tool pool and missing-manual count, confirm possible API charges, and select **Generate missing manuals (uses API)**. Valid entries are reused; each manual contains three proxy queries. For skills only, set Target to `skills`; tool manuals and a manual-generation provider are not required.
5. Try an existing tool or a relevant native skill. Adjust k or switch OFF. An explicit OFF choice remains OFF after preparation.

The plugin adds no demo tools. Configure tool sources in DSH normally. First preparation can take time for large pools.

## Optional provider-owned discovery

An optional [installable local example](https://github.com/JKL-agents/toollery-dsh/tree/main/examples/dsh/discovery-provider) registers `discover_tools`, supplies a complete candidate batch and returns ranked recommendations. Its guide covers Desktop installation, generation consent, prompts, optional skill guidance and provider adaptation. The main package does not install it automatically.

Providers that can search a remote catalog may pass a candidate batch to the service without registering that batch in DSH first:

```js
const result = await toollery.rankCandidates(
  agent.id,
  userPrompt,
  candidateBatch, // [{ schema: { name, description, parameters }, candidate_ref, provider_id, executor }]
  { representation: 'spec_manual', budget: 'top_k', k: 3, requestId: 'discovery-42' },
)

if (result.status === 'approval_required') {
  // Ask the provider's policy/UI before retrying with allowGeneration: true.
} else if (result.status === 'ranked') {
  for (const candidate of result.selected) {
    // candidate_ref is opaque and comes from the provider. The provider maps
    // selected identities back to its executor records.
    useProviderRecord(candidate.tool_id, candidate.candidate_ref)
  }
}
```

`discoverCandidates` is an alias for `rankCandidates`. Toollery normalizes and caches the candidate schemas, generates missing manuals when explicitly allowed, and applies local BM25 retrieval. A ranked response includes a `request_id`, `candidate_snapshot_hash`, `selection`, and `selected[]` entries containing `tool_id`, opaque `candidate_ref` when supplied, `spec_hash`, and score. `selected_names` remains a compatibility field; providers should use `selected[]` for identity mapping. The result is a retrieval suggestion, not execution authorization: the provider must recheck the candidate snapshot, schema hash, permissions, and executor availability before registration or execution. Toollery never fetches, authenticates, executes, or registers provider tools. The candidate batch does not change the current host pool or the current turn's frozen selection. A retry with a different request ID but the same candidate snapshot and retrieval options reuses an in-progress or completed preparation job instead of duplicating a timed-out generation request. `candidateBatch` is limited to 256 unique candidates; `maxAutoTools` defaults to 15.

## CLI alternative

The CLI needs its own supported Node/pnpm environment and Python 3.12+. Desktop's application-owned profile is not a CLI profile named `desktop`.

```sh
dsh plugin --profile web add @jkl-agents/dsh-toollery
dsh --profile web
```

For a pinned or offline install, replace the package name with the matching `.tgz` release asset path.

Use your existing profile name in place of `web`. The plugin does not install another DSH runtime. Its profile-local Python worker has no third-party Python dependencies.

Start a **new native conversation**. On startup, Toollery automatically checks the profile-local worker and performs the safe local setup when DSH's bundled Python or an existing Python 3.12+ is available. This installs the embedded worker wheel offline and creates configuration templates; it makes no model calls and does not download Python. If no usable Python is available, select **Set up local worker** in Toollery controls; Windows x64 users can explicitly allow a private runtime download. `/toollery setup` remains available for retrying setup or refreshing a same-version worker.

Use the UI form above for manual settings. Advanced users can instead edit `manual-generator.json` and create `.env` with `TOOLLERY_MANUAL_API_KEY=...` in the directory shown by `/toollery status`. Then select **Generate missing manuals (uses API)**, or run `/toollery prepare`. Preparation explicitly permits API charges. When ready, the default is ON, `spec_manual`, top-k, k=3. An explicit OFF choice is respected.

Use `/toollery on`, `/toollery off`, `/toollery mode spec_manual`, `/toollery budget top_k`, `/toollery k 3`, or the Web controls. Changes apply to the next user turn. OFF bypasses retrieval and preserves DSH's own tool restrictions, even if the worker has stopped. ON failures are explicit.

For Skills, retrieval uses metadata BM25 with explicit-name priority; the tool Representation control is disabled and its value is retained for switching targets. For Both, Representation applies only to tools. Skills do not yet use generated manuals. Top-k fills up to k unique matching skills, including explicitly named ones; fewer positive matches can yield fewer results.

Use the chevron in the controls header to hide the controls and turn cards. A small funnel button beside the composer shows ON/OFF (and `!` when attention is needed); click it to restore the panel. Folding only changes visibility: settings, current selections, results and unsaved form inputs stay intact. The browser remembers the display preference across conversations and reloads; unsaved inputs are retained only while the components remain mounted, not across reloads.

Toollery captures the root agent's registered, visible tools. It does not load tool sources again or replace providers, model catalogs, personas, or DSH_HOME. Per-turn retrieval uses only tools still allowed by the host. Original schemas go to the chat model; manuals remain local. Historical calls retain their original meaning when the current subset changes.

Each user turn freezes its settings and selection across all model steps. On the first assembly of a later turn, Toollery compares the agent's visible registry with its committed snapshot. Selection uses the downstream host-eligible assembly; restricted tools can be cached without becoming callable. Valid manuals are reused; up to 15 cache misses are generated automatically by default (`settings.json: refresh_max_misses`). Larger updates require approval in the panel or `/toollery prepare`, then waiting until ready and **resubmitting the message**. No automatic replay or OFF fallback occurs.

The panel shows progress, elapsed time and an approximate ETA after the first uncached-tool completion. Refresh waits default to 120 seconds (`refreshTimeoutMs` plugin option, milliseconds). Timeout does not cancel provider generation: later matching requests reuse the retained job. Pool instability is retried at most once. Full BM25 indexes are rebuilt after changes. Cross-process cache-lock conflicts require retry after the owner finishes. See the repository guide for these boundaries.

Native root conversations are supported. PTC (`run_code`) and subagent retrieval are not supported. Retrieval uses the latest user text, without history rewriting or recovery search. The discovery API requires a provider integration; there is no autonomous discovery loop or same-turn expansion of model-facing declarations. For dependency-heavy tasks, increase k or switch OFF.

If the host publishes a native skill catalog, the plugin applies the per-turn top-k setting using a separate metadata-only BM25 index. Only the host's catalog names and description summaries reach the local worker. The native DSH `skill` declaration stays available within host eligibility, and DSH loads the full body on demand; Toollery does not read the skill registry, copy skill files, or replace the host provider. `all` keeps the complete catalog. Hosts without a native catalog retain their normal behavior.

Retrieval has three targets: `tools` (standard tool search), `skills` (skill metadata search with all current host-eligible tools), and `both` (both searches independently). Change it with `/toollery target tools|skills|both` or the Target control. The `skills` target requires worker setup but no tool manuals/index; it does not narrow tool visibility or execution. Turn cards show selected skill names and retrieval time. Settings apply to the next turn. This is catalog routing, not a skill execution permission boundary.

The standard resident set always includes DSH's native `skill` declaration. Integrations that have additional tools which must remain visible can pass `residentToolNames` in the plugin configuration; these names are retained alongside the selected retrieval tools. This list still operates within DSH's host-eligible scope.

The bundle includes a **95-skill test pack** sourced from GitHub's [awesome-copilot](https://github.com/github/awesome-copilot) project. It registers through DSH's native skill provider, with every skill name prefixed `sample-` to avoid collisions. These are prompt-instruction files; the pack adds no runtime dependencies. Some individual instructions may describe optional workflows that use project files or external services. See [`sample-skills/README.md`](sample-skills/README.md) for attribution and license. Example tests: ask for structured meeting minutes from a short transcript, request a concise project README from a description, or ask to draft a professional email from bullet points; explicitly naming the corresponding `sample-meeting-minutes`, `sample-create-readme`, or `sample-email-drafter` skill makes loader testing unambiguous.

### Adding your own tools and skills

Toollery does not scan or register tools itself. Add tools through DSH's normal MCP or Cordis configuration, and finish loading those sources before starting a new native conversation. Toollery snapshots the tools that are registered and eligible for that agent, preserves host restrictions, and can reconcile later additions on a subsequent turn. A native tool needs a stable unique `name`, a useful `description`, a JSON `parameters` schema, and a host-owned executor; the provider remains responsible for registration, permissions, and execution.

For local skills, use DSH's filesystem skill provider. Put either `<skill-root>/<skill-name>/SKILL.md` or `<skill-root>/<skill-name>.md` in one of DSH's scanned roots: project `.dsh/skills` or `.agents/skills`, user `~/.dsh/skills` or `~/.agents/skills`, or a directory listed in the provider's `customSkillDirs`. `SKILL.md` must start with YAML frontmatter containing `name` and `description`; `whenToUse`, `metadata`, `disable-model-invocation`, and `user-invocable` are optional. The name is kebab-case and should be unique within the catalog. DSH discovers the metadata and loads the body through its native `skill` tool; Toollery only ranks the current catalog summaries.

After adding or changing a skill, wait for DSH's filesystem provider to refresh, then start a new conversation or send a new turn. If the catalog is still unchanged, check that the file is directly under a scanned root, the frontmatter is valid, and the skill is model-invocable. Toollery does not require a restart for ordinary filesystem-provider changes, but a new turn is required because each turn's selection is frozen.

To remove:

```sh
dsh plugin --profile web remove @jkl-agents/dsh-toollery
```

Restart DSH if it reports a restart requirement. Plugin-owned listeners/UI and the worker are disposed; profile-local cache and configuration remain available for reuse. See [the repository guide](https://github.com/JKL-agents/toollery-dsh/blob/main/docs/plugin.md) for configuration, upgrades and implementation boundaries.
