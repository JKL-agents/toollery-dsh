# Environment assumptions audit

Checked on Windows, 2026-10-22. These changes ship in v0.1.1; the published v0.1.0 tag and assets remain unchanged.

## Findings and changes

| Assumption | Resulting behavior |
| --- | --- |
| Tools on a developer/IDE PATH are also available in PowerShell | Web checks Node explicitly and gives installation, supported-version and terminal-restart guidance. There is no fallback to a coding agent's private runtime. |
| Finding an executable means it is usable and compatible | Node/pnpm version probes have a 10-second timeout and handle inaccessible, failed and malformed executables. Web/setup/doctor use the same checks. |
| pnpm is needed whenever Web runs | pnpm 11.x is required by setup-web; doctor does not fail an already installed Web runtime solely because pnpm is absent. |
| A runtime package.json means installation is complete | Check the pinned DSH version and its actual CLI entry. Filesystem sources also check the MCP server entry. |
| Caller cwd is the user's workspace | Generator and external host processes run from the config directory; explicit relative executable paths resolve there. Bare names use PATH. |
| Windows npm shims work like executables | Setup, generators and host wrappers share batch-shim quoting, with AutoRun and delayed expansion disabled. Complex literal arguments retain the documented restrictions. |
| Every generator uses our API key schema or must be installed at runtime | Custom commands can omit API parameters. Configured credential references are loaded explicitly, independently of chat credentials. Cache-only startup requires configuration for identity, not the generator executable or credentials. |
| Source launch errors can wait until discovery | Check MCP executable, cwd and referenced credentials, filesystem roots and plugin paths before starting the host. Source readiness still rejects partial pools. |
| The bare localhost address is sufficient to open Web | Startup instructions point to DSH's authenticated URL, including its token. Busy-port diagnostics explain how to choose another port. |

The installed Python code and shipped application assets contain no fallback to maintainer-specific absolute paths or virtual environments. Optional benchmark drivers remain source-checkout workflows: they require the separately documented datasets/dependencies; ToolTalk records its cloned source revision using Git. They are not prerequisites for normal chat/Web use.

## Verification

- Python suite: **53 passed, 3 skipped** (56 total); skipped tests require the optional ToolTalk source.
- Node suite: **6 passed**.
- Ten new regression tests cover a real child process with empty PATH, unsupported/broken/timed-out executables, incomplete runtime metadata, setup preflight, config-relative generator execution, cache-only use without a generator executable, missing MCP prerequisites, occupied ports and actual Windows batch execution with spaces.
- Real DSH offline smoke launched from outside the checkout, with an absolute config path containing spaces: **6 turns / 11 scripted model requests passed**. Repeated with PATH empty, confirming that the bundled terminal demo does not depend on a separately installed Node/pnpm. These runs used mock manuals and no paid API calls.
- Actual installed node/pnpm version probes passed on the development environment. No dependency download or reinstall was needed.

This is Windows coverage, not Linux/macOS certification or verification of arbitrary custom MCP servers/providers. Executable checks do not establish remote service availability. Installation still requires network/package-registry access; runtime requires permission to start subprocesses and bind localhost. Locally, the sandbox denied the real SDK child process; the explicitly approved unsandboxed smoke passed.
