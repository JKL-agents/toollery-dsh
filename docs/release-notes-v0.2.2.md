# Toollery for DeepSeek Harness v0.2.2

This patch makes the conversation panel less intrusive and simplifies getting started. Retrieval behavior and the supported DSH runtime are unchanged.

## Changes

- Collapse Toollery controls and turn cards to a compact ON/OFF button. Expand them in place with settings, unsaved inputs and result details retained. Folding affects display only; its preference persists in browser storage.
- Simplify the root README around Desktop installation and everyday use. Move manual demo setup and developer commands into dedicated guides.
- Record successful Desktop installation reports from the maintainer and a colleague on another computer, separately from automated verification.
- Include an optional candidate-batch discovery example and integration instructions in the source distribution. The example is installed separately; it ranks candidates without registering or executing them.
- Align the runtime-reported package version with the distribution version.

## Install or upgrade

In DSH Desktop, use **Plugins → Add plugin → Install a third-party plugin** with the URL or absolute path of this release's `jkl-agents-dsh-toollery-0.2.2.tgz` asset. Restart if requested, then select **Set up local worker** to update the embedded worker. Existing configuration and caches are retained.

The npm package is `@jkl-agents/dsh-toollery`. GitHub and npm publication are separate steps; use the release asset for this exact version until npm lists 0.2.2. The optional discovery example is available in the source archive and is not automatically installed by the main plugin.

## Validation and scope

The panel passed an isolated React/Edge interaction check covering collapse/restore, preserved drafts/results and ON/OFF state, reload persistence, disabled browser storage and zero configuration mutations from folding. The discovery example has focused tests and an isolated DSH host check. Release CI covers offline regressions, packaged-worker setup and installed-wheel smoke checks. No paid API requests are needed.

The local compatibility target remains DSH Desktop 0.12.1 with DSH runtime 0.2.1-rc.2 on Windows. Reports from another computer confirm installation, not every feature or platform. Native root conversations are supported; PTC and subagent retrieval remain outside scope. See [verification](https://github.com/JKL-agents/toollery-dsh/blob/v0.2.2/docs/verification.md).
