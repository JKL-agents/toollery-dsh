# Toollery for DSH — Proxy-query tool retrieval with Web UI controls

Hi everyone! 👋

I'd like to share **Toollery for DSH**, an independent implementation of the [Toollery](https://arxiv.org/abs/2609.22218) methodology for DeepSeek Harness.

**Repository:** [JKL-agents/toollery-dsh](https://github.com/JKL-agents/toollery-dsh)

**Release:** [v0.1.1](https://github.com/JKL-agents/toollery-dsh/releases/tag/v0.1.1)

> Unofficial community project. Independently developed and maintained; not affiliated with or endorsed by DeepSeek or the original Toollery authors.

## How it works

Toollery discovers the complete tool pool exposed to a configured DSH agent, generates and caches **three proxy queries per tool**, and uses local BM25 retrieval to select tools for each user turn.

Retrieval supports `spec`, `manual`, and `spec_manual` representations. The chat model receives the selected tools’ **original schemas**; proxy queries remain in the local retrieval index.

The integration uses a **Cordis plugin and a local Python bridge**, without modifying upstream DSH source code. It supports the bundled utility demo, a filesystem MCP preset, and configured MCP or Cordis tool sources.

## Demo: change k and try different prompts

The first recording starts with an addition request at **k=3**, then changes k to **5** for a temperature-conversion request. Per-turn cards show the retrieved candidates and actual tool calls.

![Change k and retrieve tools for different prompts](https://raw.githubusercontent.com/JKL-agents/toollery-dsh/main/DSH_Toollery_demo.gif)

## Demo: switch OFF during a conversation

Disabling Toollery bypasses retrieval and restores the **full tool pool for the next turn**. The configured k is retained but has no effect while OFF.

![Switch Toollery OFF and restore all 12 tools](https://raw.githubusercontent.com/JKL-agents/toollery-dsh/main/DSH_Toollery_demo_2.gif)

Both recordings use the bundled 12-tool demo. Settings apply to the next turn; an active turn keeps its current tool set.

## Installation and configuration

Source-based installation and usage instructions are in the [README](https://github.com/JKL-agents/toollery-dsh#install-and-try-it). Distribution currently uses GitHub releases; npm and DSH bundle distribution are deferred.

Verified on **Windows with Python 3.12 and DSH 0.2.1rc2**. Web setup requires supported Node.js and pnpm versions; see the README for prerequisites.

Chat and manual generation can use **separate models, endpoints, and API keys**. Credentials stay in the user’s environment or local `.env` file, and valid cached manuals are reused across launches.

## Evaluation and current limitations

On our documented **ToolRet development subset** of 10,656 tools and 5,679 queries, `spec_manual` improved Recall@3 from **32.52% to 37.23%**, and Recall@10 from **42.00% to 48.48%**, compared with spec-only retrieval.

An executable **ToolTalk** supplement also exposed a practical limitation: restrictive top-k selection can omit tools needed for follow-ups or dependencies. The current implementation retrieves from the latest user text, without history-aware rewriting or recovery search. A larger k or switching OFF may suit these tasks.

These are implementation experiments, rather than a reproduction of the paper’s results. See the [experiment report](https://github.com/JKL-agents/toollery-dsh/tree/main/docs/experiments) for protocols, token usage, latency measurements, and observed tradeoffs.

## Acknowledgments

Based on *Toollery: Scaling LLM Agents to Thousands of Skills and Tools* by Xiangxi Tian and Ran Guan. Please see the [authors’ original repository](https://github.com/XiangxiTian/toollery).

This integration was independently written, without reusing the original Toollery source code. The manual-generation prompt follows Appendix A.1 with attribution. This integration is released under the MIT License.

Feedback on DSH integration, practical workloads, and tool-selection behavior is welcome!
