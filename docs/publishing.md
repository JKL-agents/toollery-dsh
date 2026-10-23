# Publishing

This is a maintainer checklist. Preparing files and artifacts does not publish them.

## Authentication and identity

Use your own GitHub login locally. Do not send a password, personal access token, private key or credential-store output in an issue or chat.

On Windows, Git Credential Manager can open a browser during the first authenticated HTTPS push. Sign in to an account with write access to `JKL-agents/toollery-dsh`, and authorize organization access if GitHub requests it. `git ls-remote` against a public repository does not prove write authentication.

Alternatively, after installing GitHub CLI:

```powershell
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
```

For command-line commits, set the repository-local author identity. Use a GitHub-verified email or your GitHub-provided noreply address:

```powershell
git config --local user.name 'Jikun Liu'
git config --local user.email 'YOUR_VERIFIED_OR_NOREPLY_EMAIL'
```

The author identity controls commit metadata; it does not grant GitHub access. The repository's MIT copyright holder and package contributor are Jikun Liu.

## Release checklist

1. Review the [README](../README.md), [validation](verification.md), [experiment report](experiments/README.md), [acknowledgements](../ACKNOWLEDGEMENTS.md) and staged file list. Keep `.env`, datasets, caches, environments and private logs excluded.
2. Run the documented tests, build the wheel/source archive and DSH bundle, and run `twine check` on the Python artifacts. Test the bundle's embedded worker and its installation into an isolated existing DSH profile. Keep the installed-wheel standalone smoke. Record artifact SHA-256 checksums.
3. Obtain final maintainer approval for the concrete publication contents, then commit and push to the approved repository. Wait for the provided Windows CI checks to pass; investigate failures before tagging.
4. For a new release, tag the reviewed commit with its package version and create a GitHub Release using the matching release notes. Upload the DSH bundle, matching wheel/source archive and `SHA256SUMS.txt`. Keep existing public tags/assets unchanged. Do not publish to PyPI/npm unless that distribution decision changes.
5. Confirm the installation links and release downloads work. Enable GitHub Discussions in repository settings if desired. Review [the Discussion draft](discussion-draft.md) and obtain explicit approval before posting it; publication of a Release does not authorize posting messages to other communities.

Use the repository's existing branch policy; do not force-push or replace unrelated work. A draft Release can be prepared for review. Credentials belong in the local credential manager, never in Git remote URLs or release assets.

## Repository structure decision

No broad repository regrouping is needed for v0.2.0. `toollery/` contains the reusable retrieval/cache core; `toollery_dsh/` owns the dependency-free worker, standalone CLI and shared DSH assets; `plugin/` owns the existing-profile Cordis entry and worker lifecycle. The bundle build stages shared assets and the wheel instead of maintaining duplicate retrieval or UI implementations. The standalone SDK/runtime dependencies are optional via `[demo]`.

Keep this repository focused on the DSH distribution. Historical datasets, generated manuals, run logs, credentials and upstream checkouts stay outside the distributable tree or in ignored development directories. Existing-profile state lives in the DSH profile's `toollery/` directory; standalone state remains beside its configuration.
