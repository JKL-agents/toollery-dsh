# Toollery for DeepSeek Harness v0.3.0

v0.3.0 adds native DSH skill adaptation alongside Toollery's existing tool retrieval.

## Native skill retrieval

- Set the retrieval target to `tools`, `skills`, or `both`.
- In `skills` mode, Toollery builds a local BM25 index from the host's current skill names, descriptions and optional metadata. Explicitly named available skills receive priority and the result is deduplicated and bounded by `k`.
- DSH remains responsible for the native `skill` declaration, full skill body loading, permissions and execution. Toollery does not read or copy skill bodies and does not turn skill ranking into an execution grant.
- Skill retrieval keeps the complete host-eligible tool pool available. `both` runs the tool and skill paths independently.
- Settings and selections remain frozen for the user turn, including later model steps. Host restrictions remain effective.

The package includes 95 collision-resistant `sample-*` prompt skills for trying metadata routing and native body loading. They add no runtime packages, although individual instructions can refer to project files or optional services. See [`plugin/sample-skills/README.md`](../plugin/sample-skills/README.md) for attribution and limits.

## Tool and worker updates

- Skill-aware cards show the target, skill candidate count, applied/latest `k` and metadata retrieval time. Tool representation controls are disabled when the selected target does not use them.
- Top-k filling now handles explicit-name matches without duplicate entries or `k-1` underfilling when enough positive matches exist.
- Startup safely installs the embedded worker offline when DSH's bundled or an existing Python is available. No model call or runtime download happens automatically; the setup control remains available for retry and explicit private-runtime consent.
- Existing tool retrieval, cache-based manual generation, provider-owned discovery ranking and the standalone demo remain available.

## Compatibility and limits

The tested compatibility target is Windows with DSH Desktop 0.12.1 and DSH runtime 0.2.1-rc.2. Native root conversations are covered; PTC and subagent retrieval are outside this release's scope.

Skill retrieval is metadata-only in this release; generated skill manuals, history-aware rewriting, dependency expansion and recovery search are not included. Hosts without a native skill catalog retain their normal behavior. Tool retrieval still uses the latest user text, so increase `k` or switch OFF for tasks that need broad follow-up access.

Install from DSH Desktop with the package name `@jkl-agents/dsh-toollery`, or use the matching `.tgz` asset from the [GitHub release](https://github.com/JKL-agents/toollery-dsh/releases/tag/v0.3.0). The npm package and GitHub bundle contain the same versioned worker and UI.
