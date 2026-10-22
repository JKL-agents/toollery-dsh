# Draft: Toollery integration for DeepSeek Harness

This is a publication draft, not a posted Discussion.

I have built an independent DSH implementation of [Toollery](https://arxiv.org/abs/2609.22218): [JKL-agents/toollery-dsh](https://github.com/JKL-agents/toollery-dsh).

The integration discovers a configured agent's complete tool pool, generates and caches three proxy queries per tool, and uses local BM25 to select a per-turn subset. The official DSH Web UI includes ON/OFF, spec/manual/combined search, all/top-k settings and selected/called tool cards. Chat and manual generation can use separate models and keys. Installation and a small utility/filesystem demo are documented in English.

On a documented ToolRet development subset, combined retrieval improved Recall@3 from 32.52% to 37.23%. A separate 28-tool executable ToolTalk supplement showed the boundary clearly: fixed top-3 candidates saved tokens but reduced adapted strict turn success, especially for follow-ups and dependencies. The README reports these negative results and does not claim universal quality or latency gains.

The project is MIT-licensed, with v0.1.0 distributed through GitHub. It acknowledges [the original Toollery repository](https://github.com/XiangxiTian/toollery) and reproduces the paper's Appendix A.1 prompt with attribution. It does not reuse original Toollery source code and has no affiliation with the Toollery authors or DSH team.

Feedback on DSH source discovery, tool-scope handling and practical workloads is welcome.
