# Troubleshooting

Start with `toollery-dsh doctor --config path/to/toollery.json`; add `--require-web` for Web. Doctor makes no model calls and reports credential presence without values.

| Symptom | Action |
| --- | --- |
| Command not found | Use the environment's `Scripts/toollery-dsh.exe` on Windows or activate that environment. |
| Cannot read configuration | Pass `--config`. `init` preserves existing files; compare templates when upgrading. |
| Missing credentials | Put `.env` next to the config. Use the separate key names or shared `TOOLLERY_API_KEY`. |
| Missing manuals / snapshot HTTP 503 | Run `prepare` using the same config/interpreter/generator. Inspect `manual-preparation.json` in the report directory. |
| Changed tools fail cache-only startup | Restart, then `prepare`; new/changed tools need new manuals. |
| Generation rejected | Check model/endpoint and failure types. Exactly three valid query/thought entries are required; partial manual pools are not accepted. |
| Interrupted generation leaves a lock | Stop processes using the cache, then remove only that stale `.lock`. Locks prevent duplicate paid calls. |
| Web/MCP runtime missing | Install Node/pnpm and run `setup-web`. It uses the shipped lockfile, not global DSH packages. |
| Port busy | Stop the old server or use `--port 3081`. |
| No controls yet | Select a workspace and create/open a session; controls wait for discovery/preparation. |
| ON fails after OFF startup | Run `prepare` and restart, or explicitly permit generation at launch. |
| No matching tool / few candidates | Top-k omits zero-score tools. Restate the capability, increase k, use all or OFF. |
| Follow-up misses tools | Latest-user-text retrieval is a known v0.1 limitation. Restate the task or broaden access. |
| Tool changed during session | Restart; live registry mutation is rejected. |
| Process/network denied | Check sandbox/firewall policy. DSH needs child processes and localhost HTTP; setup needs registry access. |

`chat --offline-smoke` uses real DSH/tools with a scripted model; `web --offline` supports plumbing inspection. Neither measures model quality. Mock and real manual caches are separate.

Reports can include prompts, schemas, results and responses. Keep raw reports private; redact before sharing. Include versions, command, doctor output and error type in issues, never credentials. Error summaries avoid echoing arbitrary provider content.
