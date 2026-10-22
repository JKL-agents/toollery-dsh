# Toollery for DeepSeek Harness v0.1.2

Windows double-click installation and a browser-based demo setup. Retrieval behavior and benchmark conclusions are unchanged.

## Try it

1. Download [the v0.1.2 repository ZIP](https://github.com/JKL-agents/toollery-dsh/archive/refs/tags/v0.1.2.zip), or choose **Source code (zip)** below, and extract it to a writable folder.
2. Double-click **Start-Toollery.cmd** and keep the terminal open. The launcher installs private Python, Node.js, pnpm and DSH dependencies; no global installations or administrator rights are required.
3. On the browser setup page, choose **12 utility tools**, enable **Scripted preview**, and select **Start demo**. Try `Use add_numbers to add 19 and 23.` No API key or paid API requests are needed for this preview.

The wheel is for manual Python installation. Use the repository ZIP for the double-click entry point. See the [README](../README.md) and [launcher guide](launcher.md) for real-model setup and configuration.

## Changes

- Add Windows x64 private dependency installation without relying on preconfigured Python/Node/pnpm PATH entries.
- Add browser settings for tool pools, ON/OFF, retrieval representation, top-k, independent chat/manual credentials and explicit manual-generation permission.
- Add a free scripted utility preview with isolated mock manuals and real DSH tool execution.
- Open an authenticated DSH workspace automatically, reuse valid manuals across launches, select an available localhost port and stop owned child processes.
- Reorganize the README into free preview, real-model setup, restart/troubleshooting and optional manual installation.

## Scope and known limitations

This is a standalone demo with its own DSH environment, not yet a bundle installed into an existing DSH profile. Windows x64 is the verified launcher platform; DSH remains pinned to 0.2.1rc2. Scripted preview supports example requests, not arbitrary chat. Real chat and missing/stale manual generation use the user's configured APIs and may incur charges.

First use requires internet access and can spend several minutes installing Web dependencies after **Start demo**. The new tab displays a static preparation message during this stage; detailed installation progress is not yet shown there. Keep the setup page and terminal open. Later starts reuse installed dependencies. Each launch creates fresh session state; manuals and playground files persist.

The release includes a wheel, source archive and SHA-256 checksums. Existing releases remain unchanged. This is a GitHub release, not a PyPI/npm publication.
