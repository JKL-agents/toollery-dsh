# Manual standalone demo installation

For a plugin in your existing DSH Desktop, use the [plugin guide](plugin.md). The commands below create a separate demo environment from a source checkout.

## Prerequisites

The [Windows double-click launcher](launcher.md) installs its own dependencies. For manual installation, prepare:

- Python 3.12 or later; Python 3.12.7 was tested on Windows.
- Node.js 22.19+ on the 22.x line, or 24+, for Web and the filesystem preset. Node 23 is unsupported. pnpm 11.x is required to install the pinned Web/MCP runtime, but not to run it afterward. Node 24.19 / pnpm 11.25 were tested.
- For real model use, a chat API compatible with DSH's configured DeepSeek Messages provider, and an OpenAI Chat Completions-compatible API for manuals. Defaults use `deepseek-flash` for both; these settings are independent.

DSH is pinned to Python `0.2.1rc2` / npm `0.2.1-rc.2`. Windows is verified; Linux and macOS are not yet verified. The DSH plugin is distributed through npm and GitHub releases; the Python package is not published to PyPI.

Install [Python](https://www.python.org/downloads/), [Node.js](https://nodejs.org/en/download) and [pnpm](https://pnpm.io/installation) as needed. After installation, reopen PowerShell and check `node --version` and `pnpm --version` **in the terminal you will use to launch Web**. An IDE or coding agent may have its own tools on PATH; that does not make them available in a normal terminal. The bundled terminal demo uses the Python SDK runtime and does not require a separate Node/pnpm installation. Configured MCP servers may require additional executables.

## Terminal setup

These commands use PowerShell. Activation is optional; use the environment's executables directly if activation is restricted.

```powershell
git clone https://github.com/JKL-agents/toollery-dsh.git
cd toollery-dsh
py -3.12 -m venv .venv
& .venv/Scripts/python.exe -m pip install ".[demo]"
& .venv/Scripts/toollery-dsh.exe init my-workspace
& .venv/Scripts/toollery-dsh.exe doctor --config my-workspace/toollery.json
```

Verify the real DSH bridge with a scripted model and mock manuals, without API credentials or charges:

```powershell
& .venv/Scripts/toollery-dsh.exe chat --config my-workspace/toollery.json --offline-smoke
```

For real use, copy the template, fill credentials locally, and prepare manuals:

```powershell
Copy-Item my-workspace/.env.example my-workspace/.env
# Edit my-workspace/.env: set TOOLLERY_API_KEY, or separate chat/manual keys.
& .venv/Scripts/toollery-dsh.exe prepare --config my-workspace/toollery.json
& .venv/Scripts/toollery-dsh.exe chat --config my-workspace/toollery.json
```

`prepare` makes paid API calls for missing/stale manuals. Normal `chat`/`web` startup only reads the manual cache; chatting still uses your paid chat API. `--generate-manuals` explicitly permits startup generation. Existing valid entries are reused.

## Official Web UI

```powershell
& .venv/Scripts/toollery-dsh.exe setup-web --config my-workspace/toollery.json
& .venv/Scripts/toollery-dsh.exe doctor --config my-workspace/toollery.json --require-web
& .venv/Scripts/toollery-dsh.exe web --config my-workspace/toollery.json --port 3080
```

Open the authenticated `dsh web:` URL printed by DSH **including its `?token=...`**, select a workspace, and start a conversation. The composer shows the switch and retrieval controls. Changes apply to the next turn; a turn's subset stays fixed through its tool calls. Cards show the full pool, selected tools and actual calls. Stop with Ctrl+C. The launcher binds to `127.0.0.1`.

Try “Please use the matching tool to add 19 and 23,” then “Convert 20 degrees Celsius to Fahrenheit using a tool.” Change k or switch OFF to inspect tool availability. These examples are not benchmark evidence.

Web commands: `/toollery status`, `/toollery on`, `/toollery off`, `/toollery mode spec_manual`, `/toollery budget top_k`, `/toollery k 3`. Terminal equivalents: `/on`, `/off`, `/mode`, `/budget`, `/k`, `/tools`, `/quit`.

## Real filesystem tools

```powershell
& .venv/Scripts/toollery-dsh.exe init filesystem-demo --preset filesystem
& .venv/Scripts/toollery-dsh.exe setup-web --config filesystem-demo/toollery.json
Copy-Item filesystem-demo/.env.example filesystem-demo/.env
# Fill credentials locally.
& .venv/Scripts/toollery-dsh.exe prepare --config filesystem-demo/toollery.json
& .venv/Scripts/toollery-dsh.exe web --config filesystem-demo/toollery.json
```

The allowed directory is `filesystem-demo/playground`, created by `init`. Ask the assistant to read its `welcome.txt`, using the absolute path if needed. Change `tool_sources[0].root` to select another allowed directory. Filesystem operations execute for real within that directory.
