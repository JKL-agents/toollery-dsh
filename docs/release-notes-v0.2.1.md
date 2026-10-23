# Toollery for DeepSeek Harness v0.2.1

This patch release contains the latest Windows provider-bridge fixes and publishes the DSH bundle as `@jkl-agents/dsh-toollery` for direct installation from DSH Desktop.

## Install

In DSH Desktop, open **Plugins → Add plugin → Install a third-party plugin** and enter:

```text
@jkl-agents/dsh-toollery
```

For a pinned or offline install, download the `.tgz` asset from the [GitHub release](https://github.com/JKL-agents/toollery-dsh/releases/tag/v0.2.1) and enter its URL or absolute local path. The standalone Windows demo remains available from the repository source ZIP.

## Changes

- Detects the built-in provider from DSH profile configuration rather than Windows executable path equality.
- Keeps the provider bridge isolated from checkout paths and unrelated Python environments.
- Publishes the same built bundle through npm and GitHub release assets.
- Clarifies the Desktop, CLI, pinned archive and standalone demo installation paths.

## Scope

This release retains the v0.2.0 compatibility boundary: Windows and the pinned DSH Desktop/runtime path are verified; PTC, subagent retrieval, Linux/macOS and arbitrary plugin/provider combinations remain outside the verification claim. See [verification](verification.md).
