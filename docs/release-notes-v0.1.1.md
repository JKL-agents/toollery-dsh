# Toollery for DeepSeek Harness v0.1.1

Compatibility and startup diagnostics update. Retrieval behavior and benchmark conclusions are unchanged.

- Check Node.js and pnpm compatibility before Web setup/startup, with bounded probes and actionable PATH diagnostics.
- Resolve configured generator and host commands from the configuration directory; handle Windows npm command shims consistently.
- Validate Web runtime metadata, port availability, MCP executables/directories and referenced credentials before startup.
- Treat pnpm as a setup-only dependency and support custom generators without assuming API credentials.
- Fix the Windows CI short-path assertion and update checkout/setup actions to Node.js 24.
- Clarify prerequisites and authenticated browser URLs; add two real DSH Web UI demo recordings.

Windows with Python 3.12 and DSH 0.2.1rc2 is the verified platform. Node.js 22.19+ on 22.x or 24+, and pnpm 11.x for Web setup, are required. See the [README](../README.md) for source-based installation and the [environment audit](environment-audit.md) for the checks behind this update.

The release includes a Python wheel, source archive and SHA-256 checksums. This is a GitHub release, not a PyPI/npm publication. One-click installation and demo launchers are not included in v0.1.1.
