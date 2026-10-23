# Environment assumptions audit

## Final lifecycle and packaging follow-up (local, 2026-10-23)

- Current regression: **68 Python passes / 3 optional skips**, **16 Node passes**, and a fresh isolated real-host run of **5 turns / 9 scripted chat-provider requests**. The dynamic tool needed one mock manual; 12 valid manuals were reused. No paid APIs or user Desktop profiles were used.
- Worker readiness, the host-visible registry and downstream eligibility are distinct. The registry supplies the cache/index universe; retrieval intersects host-allowed tools. OFF retains host restrictions. A refreshed status now reports the committed worker pool even before another selection is made.
- Manual generation is not assumed to finish within a normal bridge request. Refresh has a separate configurable 120-second wait, retained jobs, progress and approximate ETA. Timeout does not prove cancellation of a remote generation request. Matching retries within one worker reuse its job; cross-process cache-lock contention is explicitly retryable.
- Pending approval is bound to the new snapshot. Approve, wait for readiness, then resubmit the user message; there is no automatic replay. Settings/selection remain fixed within a turn; a changed host pool is rechecked before selection, with a bounded stale retry.
- Replacing a development tarball at the same local path/version caused pnpm to reuse the earlier installation. The acceptance used a content-specific tarball path and verified installed file identity. Published version tags/assets must remain immutable; ship updates with a new version.
- Documentation distinguishes Desktop 0.12.1 from DSH runtime 0.2.1-rc.2, plugin installation from the standalone demo, and implemented provider APIs from autonomous discovery. Desktop GUI onboarding and Linux/macOS remain unverified.

## Desktop initialization follow-up (local)

- Desktop's official plugin manager accepts a release tarball URL or absolute local path and supplies its own Node/package manager. Users need not provision a second DSH or a development terminal. The application-owned Desktop profile must not be confused with a CLI profile named `desktop`.
- Python is not assumed to be present: optional, user-authorized Windows x64 setup downloads pinned uv, verifies SHA-256 and installs private Python 3.12.12. A fresh profile with empty PATH passed setup after consent and refused download without consent. Unsupported platforms still require Python 3.12+.
- Node fetch timed out despite Windows networking succeeding. The Windows-only download now uses absolute system PowerShell and the Windows proxy configuration, with bounded download/setup timeouts and checksum verification. No global PATH changes occur.
- The UI saves separate manual credentials in the profile and returns only redacted status. Key rotation reuses existing manuals; model/endpoint edits trigger cache checks without paid generation. The key file is local storage, not an OS keychain. Configuring a manual provider is independent of chat provider configuration.
- Targeted onboarding checks passed (3 Python, 4 Node). The installed Desktop bundled-runtime path is covered by the separate acceptance profile below; packaged Desktop GUI restart and visual onboarding remain unverified. The private-runtime test is an empty-PATH simulation on Windows, not clean-machine certification.

## v0.2.0 existing-profile plugin (local release verification)

Checked on Windows, 2026-10-22. This audit covers local environment assumptions and the actual distribution, not only declared package versions. No paid API requests were made.

### Findings and changes

| Assumption | Finding and treatment |
| --- | --- |
| Running from a checkout proves the worker was installed | Found an actual false positive: the private venv contained only pip, while imports borrowed repository source. Setup now uses Python isolated mode, a profile-local cwd, isolated pip configuration, and forced installation of the embedded wheel. It verifies both the installed distribution version and that the worker module resides inside the venv. |
| Host Python paths/configuration cannot affect private workers | Worker startup ignores `PYTHONPATH`, `PYTHONHOME`, current-directory imports and user site packages. The built-in provider subprocess also uses isolated imports. Custom generator commands retain their explicit environment contract. Credentials are still inherited normally. |
| Windows users with Python 3.13+ also have `py -3.12` or `python` | Added version-checked `py -3` discovery between those candidates. Absolute `TOOLLERY_PYTHON` or plugin `python` configuration remains supported. No maintainer/IDE runtime fallback is shipped. |
| Updating the bundle necessarily updates an existing worker | Saved worker metadata includes its version. A mismatch requests explicit setup. Repeated setup reinstalls the embedded wheel and preserves configuration/manuals; it does not call a model. |
| Python SDK/runtime dependencies are needed by the plugin | Core wheel has no third-party dependencies. SDK/runtime remain in the standalone `[demo]` extra; benchmark dependencies remain optional. Plugin setup installs only the embedded wheel with no index or dependency resolution. |
| Cordis and LLM packages should be installed again in the profile | Bundle declares optional host peers; DSH supplies those services through its existing resolver. A fresh profile installed only the Toollery bundle, reported no peer issues, and loaded it in the actual host. Compatibility remains pinned to DSH `0.2.1-rc.2`. |
| Setup and generation share all prerequisites | Bundle installation uses the existing DSH/pnpm installation. Worker setup requires Python 3.12+, writable profile storage and working venv/ensurepip; it is offline. Generation additionally needs the configured provider command, endpoint and credentials. Cache validation makes no provider calls. |

The plugin never rewrites global PATH, installs DSH again, or changes the host chat credentials/model configuration. A supported Node.js version, permission to spawn processes and bind localhost, and an already usable DSH profile remain explicit prerequisites. Worker ports are dynamically assigned. Custom generators, proxies, TLS certificates and remote MCP/provider availability remain user/environment-specific dependencies.

DSH Desktop runtime reuse was checked against the installed Desktop 0.12.1 layout and the official `dsh-desktop-office` contract. The plugin patch reuses Desktop's existing `DSH_DESKTOP_OFFICE_RESOURCES` absolute path injection; setup resolves `office-runtime/primary-runtime`, creates a profile-local venv with `--without-pip`, and installs the embedded wheel using the bundled pip. A direct run against the installed Desktop Python 3.12.14 passed worker import/version/isolation checks. The official workspace-dependency service remains an optional host capability; Toollery does not require the Office bundle or write to its payload.

### Verification

- Python: **60 passed, 3 optional ToolTalk skips**. Node: **10 passed**, including one shipped-bundle end-to-end test.
- The bundle test creates a fresh private venv in a path with spaces, verifies the installed module location/distribution, and runs with conflicting `PYTHONPATH`, `PYTHONHOME`, `PIP_TARGET` and a same-name module in the profile. It covers the actual built-in provider subprocess against a local HTTP fixture, two manual-generation requests, cache reuse, version mismatch and repeated setup without further requests.
- Official `dsh plugin add` into a fresh isolated profile succeeded. `pnpm peers check` found no issues. The profile worker used **Python 3.13.4**, while the isolated bundle test used **Python 3.12.7**. Both contained only pip and Toollery; the real profile also passed `pip check`.
- Real DSH native acceptance from outside the checkout: **4 turns / 7 scripted provider requests** passed. Current subsets, original history, host restrictions in ON/OFF, host prompt/model catalog and one downstream middleware/provider dispatch per request were preserved. The acceptance driver now checks and records the installed worker location/version/dependencies, preventing the earlier source-import false positive.
- A separate `desktop-acceptance-20261023` profile ran the same host acceptance with the installed DSH Desktop 0.12.1 `office-runtime/primary-runtime` (Python **3.12.14**) injected through `DSH_DESKTOP_OFFICE_RESOURCES`. The profile-local venv contained only the Toollery distribution because Desktop's setup path intentionally uses `venv --without-pip`; the four-turn/seven-request acceptance passed without paid model calls.
- Wheel/sdist passed `twine check`. Archive inspection found no credentials, virtual environments, datasets or local runs, and the plugin bundle contained one matching worker wheel. Wheel metadata has no mandatory third-party Python requirements; host peers are optional and the DSH version is pinned.

Earlier source-based worker success alone was insufficient evidence of installation. The corrected installed-worker tests and real-host rerun above supersede that interpretation. Prior Web registration/route checks remain registration coverage, not manual browser rendering of the new plugin. Linux/macOS, arbitrary providers/plugins and clean-machine bootstrap are not certified by this audit.

## v0.1.1 standalone audit (historical)

Checked on Windows, 2026-10-22. The following changes shipped in v0.1.1; the published v0.1.0 tag and assets remained unchanged.

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
