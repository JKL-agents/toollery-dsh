# Toollery for DeepSeek Harness

A DSH plugin implementing [Toollery](https://arxiv.org/abs/2609.22218) for both registered tools and DSH's native skill catalog. For tools it generates and caches **three proxy queries per tool**, then uses local BM25 retrieval to select tools for each user turn. For skills it ranks only DSH's name/description metadata; DSH's native `skill` loader still owns full bodies and execution. The chat model receives original tool schemas, and manuals stay in the local index.

Independently developed by Jikun Liu. Not affiliated with or endorsed by DeepSeek or the original Toollery authors. Based on [XiangxiTian/toollery](https://github.com/XiangxiTian/toollery); see [Acknowledgements](ACKNOWLEDGEMENTS.md).

## Install and try it

### In your existing DSH Desktop

Open **Plugins → Add plugin → Install a third-party plugin** and enter:

```text
@jkl-agents/dsh-toollery
```

1. Install and enable the plugin; restart if DSH requests it. It uses your existing chat model and tools—no second DSH installation is needed.
2. On startup, Toollery automatically checks and installs its profile-local worker when DSH's bundled Python or an existing Python 3.12+ is available. This local setup makes no model calls and does not download Python. If no usable Python is available, open Toollery's plugin details and select **Set up local worker**; Windows x64 users can explicitly allow a private Python download. Skills mode does not require tool manuals or a manual-generation API.
3. **For tool retrieval or `both`:** save the manual-generation model, OpenAI Chat Completions-compatible API base URL and key. These can differ from your DSH chat credentials and are stored locally.
4. **For tool retrieval or `both`:** start a new native conversation (Standard preset), review the tool count and possible API charges, then select **Generate missing manuals (uses API)**. Valid cached manuals are reused. For skills only, set Target to `skills`; no manual-generation credentials or generation step is needed.
5. Ask for a registered tool or a relevant native skill. Inspect the selected/called cards, change k, or switch OFF.

No terminal or `.env` editing is required. The plugin adds no demo tools. First preparation may take time for large pools; progress and an approximate ETA are shown.

**If DSH reports a missing npm metadata/cache blob while installing or reinstalling the plugin**, this can be a local package-manager cache inconsistency even when the published package is valid. If the message names npm, close DSH, run `npm cache clean --force` in PowerShell, restart DSH and retry. This clears npm's cache, not Toollery's profile data or API settings. If DSH still reports a cache error, install the `.tgz` from the [latest GitHub release](https://github.com/JKL-agents/toollery-dsh/releases/latest) and include the exact error when reporting it; see [troubleshooting](docs/troubleshooting.md#existing-profile-plugin).

Desktop installation has been reported successful by the maintainer and a colleague on another computer. The local compatibility target is **DSH Desktop 0.12.1 / DSH runtime 0.2.1-rc.2**; see the [verification summary](docs/verification.md) for scope.

For **CLI installation, pinned `.tgz` releases, upgrades and removal**, see the [plugin guide](docs/plugin.md). npm resolves the current published package; downloadable bundles are on the [latest GitHub release](https://github.com/JKL-agents/toollery-dsh/releases/latest).

### Try without an existing DSH: Windows demo

Download **Source code (zip)** from the [latest release](https://github.com/JKL-agents/toollery-dsh/releases/latest), extract it, and double-click **`Start-Toollery.cmd`**. Keep the terminal open while private dependencies install.

On the setup page, choose **12 utility tools** and **Scripted preview**, then select **Start demo**. No API key or paid requests are needed. Try `Use add_numbers to add 19 and 23.`; the result should be **42**. The scripted preview supports example prompts, not arbitrary chat.

This is a separate demo environment, not a plugin installation. See the [launcher guide](docs/launcher.md) for real models, filesystem tools, startup progress and stopping the demo, or the [manual setup guide](docs/standalone.md) for terminal/Web commands.

## Everyday use

- **Default after preparation:** ON / `spec_manual` / top-k / k=3. Choose `spec`, `manual` or `spec_manual`; use `all` to retain all host-allowed tools.
- **ON/OFF:** OFF bypasses retrieval while preserving DSH's tool restrictions. Settings apply to the next user turn; a turn's selection stays fixed through its model steps.
- **Cache updates:** registered tool changes reconcile on a later turn without restarting. Up to 15 missing manuals are generated automatically by default; larger batches require approval and resubmitting the message. See [generation policy](docs/plugin.md#changes-during-a-conversation).
- **Compact panel:** collapse controls and turn cards to a small ON/OFF button, then restore them without changing retrieval settings or losing draft inputs.
- **Skill adaptation:** set Target to `skills` to rank the current native skill catalog while keeping every host-eligible tool available. Use `tools` for tool retrieval or `both` for both paths. The skill body is loaded by DSH only after the selected native skill is invoked.

<details>
<summary>Watch the demos: change k, then switch OFF</summary>

Both recordings use the standalone 12-tool pool. The first changes k from 3 to 5 between prompts; the second switches OFF to restore all 12 tools for the next turn.

![Change k and retrieve tools for different prompts](DSH_Toollery_demo.gif)

![Switch Toollery OFF and restore all 12 tools](DSH_Toollery_demo_2.gif)

</details>

## Results and boundaries

On our **ToolRet development subset** (10,656 tools / 5,679 queries), spec+manual improved Recall@3 from **32.52% to 37.23%** and Recall@10 from **42.00% to 48.48%**, compared with spec-only retrieval. These are implementation experiments, not a reproduction of the paper.

Retrieval uses the latest user text, without history rewriting or recovery search. Tight top-k settings can miss follow-up or dependency tools: the ToolTalk supplement reduced tokens but also reduced strict adapted turn success from **67/79 OFF to 51/79 ON**. Increase k or switch OFF when broader access is needed. Token savings do not imply universal latency or quality gains.

Native root conversations are supported; PTC and subagent retrieval are outside this version's scope. Toollery observes tools supplied by DSH; it does not autonomously discover or install tools. An optional [candidate-batch discovery example](examples/dsh/discovery-provider/README.md) demonstrates provider-owned discovery and ranking without registering or executing candidates.

When the host publishes a native skill catalog, Toollery can narrow its name/description summaries with metadata-only BM25 retrieval. DSH's native `skill` tool still loads the full body; Toollery never reads skill bodies or changes skill-provider permissions. Hosts without a native catalog retain their normal behavior.

The plugin bundle includes 95 `sample-*` prompt skills to try metadata retrieval and DSH's native on-demand loader. They add no runtime packages, but some instructions refer to project files or optional external services. For example, ask DSH to use `sample-meeting-minutes` to turn a short transcript into decisions and action items. Source and license details are in [`plugin/sample-skills/README.md`](plugin/sample-skills/README.md).

For skill-only testing, set Toollery's retrieval target to `skills` (UI Target control or `/toollery target skills`). This keeps the complete host-eligible tool pool and only narrows the skill catalog. Use `tools` for normal tool retrieval, or `both` to evaluate both paths together.

## Documentation

| Guide | Contents |
| --- | --- |
| [Plugin guide](docs/plugin.md) | Desktop/CLI setup, generation policy, compatibility, upgrade and removal |
| [Configuration](docs/configuration.md) | Models, credentials and cache identities |
| [Standalone demo](docs/launcher.md) · [Manual setup](docs/standalone.md) | Windows launcher, real models and terminal/Web demos |
| [Tool sources](docs/integration.md) · [Troubleshooting](docs/troubleshooting.md) | MCP/Cordis sources and diagnostics |
| [Verification](docs/verification.md) · [Experiments](docs/experiments/README.md) | Installation checks, retrieval quality, tokens, latency and limitations |
| [Development](docs/development.md) · [Publishing](docs/publishing.md) | Repository layout, build/test commands and release workflow |
| [Changelog](CHANGELOG.md) | Published and unreleased changes |

## License

MIT for original code, copyright 2026 Jikun Liu. Third-party material retains its own terms; see [LICENSE](LICENSE) and [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md).
