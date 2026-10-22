# Toollery for DeepSeek Harness v0.1.0

Initial independent DSH implementation of Toollery's proxy-query retrieval method.

- Installable Python CLI with an isolated retrieval/cache core and pinned DSH 0.2.1rc2 integration.
- Configurable, independent chat and manual-generation providers, with local environment-file credentials.
- Three cached proxy queries per tool; BM25 spec/manual/combined retrieval; ON/OFF and all/top-k controls.
- Official DSH Web UI controls, selected/called tool cards, utility demo and filesystem MCP preset.
- Complete configured-source discovery, per-turn frozen schemas and incremental cache invalidation on restart.
- English installation/integration guides, MIT license and explicit upstream attribution.
- ToolRet development results and an executable ToolTalk OFF/ON supplement, including negative results and cost/latency boundaries.

GitHub source and release artifacts are the distribution channels. This is not yet an npm, PyPI or DSH bundle release. Windows/Python 3.12 is the verified platform. Linux/macOS and arbitrary existing-host presets are not certified.

The default is ON / spec_manual / top_k / k=3. Latest-query retrieval may omit tools required by conversation history or dependencies. The pool is frozen until the agent restarts. Manual preparation fails on missing entries unless generation is explicitly allowed. Read the [README](../README.md) and [experiments](experiments/README.md) before choosing settings for your workload.

This project is not affiliated with or endorsed by the original Toollery authors or DeepSeek. The original method and repository are acknowledged; the Appendix A.1 prompt is reproduced with attribution. No original Toollery source code is incorporated.
