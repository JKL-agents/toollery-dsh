# Configuration

For the **existing-profile plugin**, run `/toollery setup` and use the configuration directory shown by `/toollery status`. It contains `manual-generator.json`, `.env` and `settings.json`; no `toollery.json` is required. Chat models and keys remain configured in DSH. See the [plugin guide](plugin.md).

Plugin `settings.json` defaults include `workers: 8` (1–8) and `refresh_max_misses: 15` (0–1,000). The latter limits automatic **cache misses** per refresh; 0 requires approval for each new manual. Configure before starting DSH. The separate Cordis plugin option `refreshTimeoutMs` sets the turn's refresh wait budget (default 120,000 ms; range 1,000–1,800,000). A wait timeout retains the background generation job; it is not provider cancellation. The Web panel displays progress, elapsed time and approximate ETA. Initial preparation remains explicitly authorized regardless of this automatic-refresh limit.

For the **standalone demo**, run `toollery-dsh init path/to/workspace`. Put `.env` next to `toollery.json`. Relative paths resolve against that directory, not the shell's current directory. Installed package resources contain application code; caches, DSH state and reports live in the user workspace.

## Standalone chat: toollery.json

| Field | Default | Meaning |
| --- | --- | --- |
| `model` | `deepseek-flash` | DSH chat model |
| `base_url` | `https://api.deepseek.com/anthropic` | Messages-compatible chat endpoint base |
| `api_key_env` | `TOOLLERY_CHAT_API_KEY` | Variable name, never the secret itself |
| `max_tokens` | `4096` | Chat output budget |
| `request_timeout_seconds` | `180` | DSH request timeout |
| `generator_config` | `manual-generator.json` | Independent generator configuration |
| `cache_dir` | `.toollery/cache` | Persistent manual records |
| `dsh_home`, `web_home` | `.toollery/dsh-cli`, `.toollery/dsh-web` | DSH state |
| `state_dir` | `.toollery/ui-state` | Web settings and audit state |
| `representation` | `spec_manual` | `spec`, `manual`, `spec_manual` |
| `budget` | `top_k` | `top_k` or `all` |
| `k` | `3` | Positive integer; zero-score top-k matches are omitted |
| `tool_sources` | omitted | Omit for demo tools; see integration guide |

Commands accept `--mode`, `--budget`, `--k`. Workers default to 8 (supported range 1–8). `--toollery-off` starts without retrieval. CLI preferences are per launch; Web settings persist for the corresponding agent/session.

The bundled launcher uses DSH's `deepseek-official` provider and ordinary complete-list tool updates. Filling another URL does not add arbitrary protocol support. Use the advanced host integration for another DSH provider, subject to its tool-projection contract.

## Manuals: manual-generator.json

```json
{
  "provider": "command",
  "command": ["{python}", "-m", "toollery_dsh.provider_bridge"],
  "model": "deepseek-flash",
  "parameters": {
    "api_url": "https://api.deepseek.com",
    "api_key_env": "TOOLLERY_MANUAL_API_KEY",
    "temperature": 0,
    "timeout_seconds": 120,
    "retries": 2
  },
  "bridge_version": "openai-compatible-v1-paper-a1",
  "timeout_seconds": 400
}
```

`{python}` becomes the current interpreter. The bridge calls `/chat/completions`; bases ending in `/v1` are accepted. It uses normalized tool evidence and the Appendix A.1 prompt, validating exactly three query/thought entries. Only query text is indexed; thoughts remain in the cache. The count is fixed at three.

Custom generator commands run from the plugin's configuration directory, or the directory containing `toollery.json` for the standalone demo. Relative script arguments resolve there. Bare executable names use PATH; explicit relative executable paths (such as `./tools/generator.exe`) resolve there too. `{python}` avoids assuming a `python` command or activated virtual environment. A custom command may omit `parameters.api_key_env` if it does not need an API key; when supplied, that variable is loaded from the config directory `.env`. Windows batch commands use the same argument restrictions as the [host bridge](integration.md).

The generator executable is required only when generation is enabled. Cache-only startup still needs its configuration for cache identity, but does not require the external generator to be installed or its API credentials to be present. Doctor reports generator executable availability as a separate optional check.

## Credentials

```dotenv
# Shared fallback:
TOOLLERY_API_KEY=replace-locally
# Or independent services/accounts:
TOOLLERY_CHAT_API_KEY=replace-locally
TOOLLERY_MANUAL_API_KEY=replace-locally
```

Separate keys override the fallback. Process environment overrides `.env`. A custom variable name must be supplied explicitly; shared fallback applies only to the default two names. Keys travel in child environments, not command arguments. The loader reads only requested variables, without shell evaluation. Do not put secrets in JSON or commit `.env`.

## Startup and updates

```mermaid
flowchart LR
  S[Complete tool snapshot] --> N[Normalized evidence]
  N --> C{Valid cache identity?}
  C -->|yes| R[Reuse manual]
  C -->|no, generation allowed| L[LLM: three proxy queries]
  L --> V[Validate and cache]
  R --> I[Build three BM25 indexes]
  V --> I
  U[Latest user text] --> Q[Search selected representation]
  I --> Q
  Q --> K[Top-k or all IDs]
  K --> D[DSH receives original schemas]
```

Normalization uses description/name/parameters and available documentation, excludes evaluation metadata, and caps evidence at 24,000 characters. Missing descriptions use structured evidence directly, without a separate LLM description-generation stage. Normalized evidence is transient; its hash and source mode are persisted, not its full text.

Cache identity: `format_version`, `tool_id`, `spec_hash`, `evidence_hash`, `normalization_version`, `manual_prompt_version`, `generator`, `proxy_query_count`, `language_policy`. Records add `manual_key`, `manual_hash`, `source_mode`, query/thought entries, usage and creation time. The generator contract includes its command and settings, so changing interpreters or provider configuration can conservatively invalidate entries.

Every startup discovers the current complete pool. Unchanged tools reuse manuals; new tools and changed public schemas need new entries; removed tools leave the new indexes. Old cache files are retained. Diff detection includes description and parameters, not just names. Internal implementation changes with identical schemas are outside this boundary.

Each user turn freezes its settings and selection across all model steps. At the first assembly of a later turn, the plugin reconciles the agent-visible registry, then retrieves within downstream `assembled.tools` eligibility. Restricted but visible schemas may be cached; this never makes them callable. Removed tools leave the next index and cannot be revived during a frozen turn. New/changed tools reuse valid entries or trigger bounded generation. Larger refreshes require preparation/approval, waiting until ready, and resubmitting the message. There is no automatic replay, OFF fallback or silent spec fallback. The standalone demo still requires `prepare` before ON initialization. OFF bypasses retrieval and exposes the host-allowed original specs. ON/all ranks the allowed pool but retains every tool, including zero-score entries.

Standalone offline runs have separate mock caches. Reports under the configuration's `runs/` directory (plugin) or `.toollery/runs/` (standalone) can include prompts, tool specs/results and responses; share them only after intentional review/redaction.
