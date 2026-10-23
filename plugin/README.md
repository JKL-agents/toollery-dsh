# Toollery for DSH

An independently written DSH plugin implementing [Toollery](https://arxiv.org/abs/2609.22218). MIT-licensed, by Jikun Liu. Not affiliated with DeepSeek or the original Toollery authors. See the [original project](https://github.com/XiangxiTian/toollery).

## DSH Desktop

The local target is **DSH Desktop 0.12.1** with DSH runtime **0.2.1-rc.2**, a working chat model and native tool presentation (Standard preset). Isolated host checks passed; Desktop GUI onboarding remains unverified.

1. Open **Plugins → Add plugin → Install a third-party plugin**. Enter the `.tgz` URL or downloaded bundle's **absolute local path**. Install, enable, and restart if requested. Desktop supplies its own Node/package manager.
2. Open Toollery's plugin details and select **Set up local worker**. DSH Desktop reuses its bundled Python runtime in a profile-local environment; it does not install another DSH/Python runtime or modify PATH. Windows x64 users without a usable bundled/system Python can allow a private runtime download. Other platforms require Python 3.12+ with venv/pip. Setup makes no model calls.
3. Save your manual-generation model, OpenAI-compatible API base URL and API key in the plugin form. DSH retains its own chat configuration. The key stays in a local profile file, not an OS keychain; it is not returned to the form. Blank retains an existing key.
4. Start a **new native conversation**. Review its tool pool and missing-manual count, confirm possible API charges, and select **Generate missing manuals (uses API)**. Valid entries are reused; each manual contains three proxy queries.
5. Once ready, try your existing tools. Adjust k or switch OFF. An explicit OFF choice remains OFF after preparation.

The plugin adds no demo tools. Configure tool sources in DSH normally. First preparation can take time for large pools.

## Optional provider-owned discovery

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
dsh plugin --profile web add /absolute/path/jkl-agents-dsh-toollery-0.2.0.tgz
dsh --profile web
```

Use your existing profile name in place of `web`. The plugin does not install another DSH runtime. Its profile-local Python worker has no third-party Python dependencies.

Start a **new native conversation**. Select **Set up local worker** in Toollery controls, or run `/toollery setup`. Setup installs the embedded worker wheel offline and creates configuration templates; it makes no model calls.

Use the UI form above for manual settings. Advanced users can instead edit `manual-generator.json` and create `.env` with `TOOLLERY_MANUAL_API_KEY=...` in the directory shown by `/toollery status`. Then select **Generate missing manuals (uses API)**, or run `/toollery prepare`. Preparation explicitly permits API charges. When ready, the default is ON, `spec_manual`, top-k, k=3. An explicit OFF choice is respected.

Use `/toollery on`, `/toollery off`, `/toollery mode spec_manual`, `/toollery budget top_k`, `/toollery k 3`, or the Web controls. Changes apply to the next user turn. OFF bypasses retrieval and preserves DSH's own tool restrictions, even if the worker has stopped. ON failures are explicit.

Toollery captures the root agent's registered, visible tools. It does not load tool sources again or replace providers, model catalogs, personas, or DSH_HOME. Per-turn retrieval uses only tools still allowed by the host. Original schemas go to the chat model; manuals remain local. Historical calls retain their original meaning when the current subset changes.

Each user turn freezes its settings and selection across all model steps. On the first assembly of a later turn, Toollery compares the agent's visible registry with its committed snapshot. Selection uses the downstream host-eligible assembly; restricted tools can be cached without becoming callable. Valid manuals are reused; up to 15 cache misses are generated automatically by default (`settings.json: refresh_max_misses`). Larger updates require approval in the panel or `/toollery prepare`, then waiting until ready and **resubmitting the message**. No automatic replay or OFF fallback occurs.

The panel shows progress, elapsed time and an approximate ETA after the first uncached-tool completion. Refresh waits default to 120 seconds (`refreshTimeoutMs` plugin option, milliseconds). Timeout does not cancel provider generation: later matching requests reuse the retained job. Pool instability is retried at most once. Full BM25 indexes are rebuilt after changes. Cross-process cache-lock conflicts require retry after the owner finishes. See the repository guide for these boundaries.

Native root conversations are supported. PTC (`run_code`) and subagent retrieval are not supported. Retrieval uses the latest user text, without history rewriting or recovery search. The discovery API requires a provider integration; there is no autonomous discovery loop or same-turn expansion of model-facing declarations. For dependency-heavy tasks, increase k or switch OFF.

To remove:

```sh
dsh plugin --profile web remove @jkl-agents/dsh-toollery
```

Restart DSH if it reports a restart requirement. Plugin-owned listeners/UI and the worker are disposed; profile-local cache and configuration remain available for reuse. See [the repository guide](https://github.com/JKL-agents/toollery-dsh/blob/main/docs/plugin.md) for configuration, upgrades and implementation boundaries.
