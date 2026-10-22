# Windows launcher

The double-click launcher is included in **v0.1.2**. [Download the v0.1.2 ZIP](https://github.com/JKL-agents/toollery-dsh/archive/refs/tags/v0.1.2.zip) and extract it before starting. Existing v0.1.1 release assets remain unchanged.

This launcher creates a standalone demo with its own DSH environment. It does not install a bundle into an existing DSH profile or certify compatibility with that profile's other plugins. Existing-host support currently uses the opt-in wrapper described in [integration](integration.md).

## Start

Extract the repository ZIP into a writable folder, then double-click `Start-Toollery.cmd`. Keep its terminal open. First use needs internet access to GitHub, nodejs.org, PyPI and the npm registry. The script downloads pinned uv, private Python 3.12.12, Node.js 22.19.0 and pnpm 11.25.0, then installs Toollery and DSH. uv and Node archives are checked against pinned SHA-256 hashes. Nothing is installed globally; Windows x64 is supported.

The setup page opens in your default browser. Choose settings and select **Start demo**. Initial Web/MCP installation may take several minutes; later starts reuse the installation. If the new tab does not open, select **Open DSH demo**. DSH normally enters the workspace automatically; if that fails, use its workspace picker. Accept DSH's own first-run notice if shown.

## Choose a demo

| Pool | Behavior |
| --- | --- |
| 12 utility tools | Calculations and text transformations. Real chat requires a DSH-compatible API. |
| Filesystem playground | Real filesystem MCP tools, limited to the generated playground directory. Requires a real chat API. |
| Custom configuration | Uses an existing `toollery.json`, its sources, model settings and credential references. Launcher API fields are ignored. |

**Scripted preview** works only with the utility pool. It requires no provider key and makes no paid API requests. Try `Use add_numbers to add 19 and 23.`, then `Use celsius_to_fahrenheit to convert 20 Celsius.` The model is scripted, so unrelated requests are not supported. Mock manuals use a separate cache and cannot satisfy real manual preparation.

Default retrieval settings are ON / spec_manual / top-k / k=3. OFF provides the full pool and ignores k. All retains and ranks the full pool. Settings apply to the next launch; stop the running demo before changing them. The DSH conversation also provides controls for subsequent turns.

The default DSH port is **3080**. If occupied, a free localhost port is chosen and displayed. The setup page uses a separate automatically selected port. Both bind only to `127.0.0.1`.

## Real models and manual generation

Enter the chat model, Messages-compatible base URL and key. Defaults use `deepseek-flash` and `https://api.deepseek.com/anthropic`. Manual generation independently uses an OpenAI Chat Completions-compatible API; defaults are `deepseek-flash` and `https://api.deepseek.com`. Expand **Separate manual-generation model or key** to change them. A blank manual key uses the chat key unless a separate key was previously saved. Blank fields retain saved keys.

For first real use with Toollery ON, check **Allow API calls to generate missing or stale manuals**. Valid cached entries are reused. Without permission, missing manuals fail preparation explicitly; there is no silent fallback. Chat requests also incur provider usage. OFF bypasses manual preparation; turning ON later requires a valid cache or generation permission.

The setup page stores credentials in local plaintext `.env`, under `.toollery-launcher/workspace`. Keys are not returned by the status API or stored in `settings.json`. Protect that folder as you would any local credential file. To remove a saved key, stop the launcher and edit its `.env` file.

## Files, restart and update

Everything defaults to `.toollery-launcher/` inside the extracted repository:

| Path | Contents |
| --- | --- |
| `tools/`, `venv/`, `uv-cache/` | Private dependencies and download/build cache |
| `workspace/settings.json`, `workspace/.env` | Saved launch settings and model credentials |
| `workspace/demo/` | Utility configuration, manuals and reports |
| `workspace/filesystem/playground/` | Files accessible to the filesystem preset |
| `workspace/filesystem/.toollery/` | Filesystem runtime, manuals and reports |

**Stop demo** stops the owned DSH process and its child processes. Ctrl+C also stops the setup server. Closing a browser tab alone does not stop them. Each start creates fresh DSH session state so requested startup settings take effect; cached manuals and playground files persist. This launcher is for repeatable demos, not resuming chat history.

Update the source in the same folder and run the launcher again. Changed package sources are reinstalled; unchanged sources reuse the private environment. Keep `.toollery-launcher/` to preserve data. To reset completely, stop the launcher and remove that directory; this also removes credentials and playground files. A custom configuration keeps its own cache and reports beside that config, with temporary launcher configuration removed on stop.

## Script options and troubleshooting

From PowerShell in the repository:

```powershell
# Download/install without opening the setup page:
& scripts/start-toollery.ps1 -InstallOnly
# Fixed setup-page port (separate from the DSH port chosen in the form):
& scripts/start-toollery.ps1 -NoOpen -Port 3095
# Choose a different private installation directory:
& scripts/start-toollery.ps1 -StateDirectory 'D:\Toollery demo'
```

The double-click entry point supplies a process-local execution-policy option; it does not alter machine policy. Organization-enforced policies can still prevent scripts or downloads. Download failures are shown in the terminal; check network access and retry. Installation reuses completed downloads. If Windows blocks a downloaded ZIP's scripts, unblock the ZIP through its Properties before extracting again.

If manual preparation fails, check model/API settings, then stop and restart with generation enabled. Details are in the selected pool's `.toollery/runs/`. For advanced custom sources and their executable prerequisites, see [integration](integration.md). The launcher supplies Python/Node/pnpm only; arbitrary MCP servers may require other runtimes.

An existing Python installation can also run `toollery-dsh launcher --directory PATH`, but that command expects Web prerequisites on its process PATH. Use `Start-Toollery.cmd` to get the private dependencies automatically.
