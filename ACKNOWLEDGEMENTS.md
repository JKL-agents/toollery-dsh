# Acknowledgements and third-party sources

Toollery for DeepSeek Harness is an independent implementation and integration of the Toollery method by **Xiangxi Tian and Ran Guan**:

- Paper: [Toollery: Scaling LLM Agents to Thousands of Skills and Tools](https://arxiv.org/abs/2609.22218).
- Original implementation: [XiangxiTian/toollery](https://github.com/XiangxiTian/toollery).

The retrieval, cache and integration code in this project was written independently. No code from the original Toollery repository was incorporated. The **manual-generation system prompt is reproduced from Appendix A.1 of the paper**, with PDF line wrapping removed and the requested count substituted. Evidence normalization, transport, cache contracts and BM25 implementation are local implementation choices. This is a DSH implementation of the approach, not a full reproduction of the paper's experiments or an original algorithm claim.

This project is **not affiliated with or endorsed by the original Toollery authors or the DeepSeek Harness team**.

## Dependencies and evaluation sources

| Source | Use | Distribution boundary |
| --- | --- | --- |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Official SDK, runtime, Web UI and Cordis services; MIT | Installed as dependencies. Verified upstream commit `687f282e8625c6b8d11ff58211a90524be2e4317`; no upstream source modifications. |
| [MCP TypeScript SDK and servers](https://github.com/modelcontextprotocol/servers) | Filesystem MCP preset; MIT | Installed through the pinned npm lockfile; not vendored. |
| [uv](https://github.com/astral-sh/uv), [Node.js](https://nodejs.org/), [pnpm](https://pnpm.io/) | Private Windows launcher runtimes and installation tools | Downloaded at setup; not vendored. Their own licenses and notices apply. |
| [ToolRet](https://huggingface.co/datasets/mangopy/ToolRet-Tools) | Optional retrieval evaluation data | Downloaded separately; dataset and its constituent source terms apply. No raw data or generated benchmark manuals included. |
| [ToolTalk](https://github.com/microsoft/ToolTalk) | Optional executable evaluation, using upstream executor and API checks; MIT | Cloned separately at `e05f4ce6132c80ed33392b81535b077d56ab28fd`. No upstream code or data vendored; local scorer adaptation is documented. |

The MIT license in this repository covers this project's original code. Third-party code, datasets and quoted paper material retain their own rights and terms. See installed dependencies for their complete notices.
