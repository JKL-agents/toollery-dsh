# Publishing v0.1.0

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
2. Run the documented tests, build wheel/source archive and run `twine check`. Install the wheel in a fresh environment and run the real DSH offline smoke from outside the source checkout. Record artifact SHA-256 checksums.
3. Obtain final maintainer approval for the concrete publication contents, then commit and push to the approved repository. Wait for the provided Windows CI checks to pass; investigate failures before tagging.
4. Tag the reviewed commit `v0.1.0` and create a GitHub Release using [release notes](release-notes-v0.1.0.md). Upload the wheel, source archive and `SHA256SUMS.txt`. Do not publish to PyPI/npm in this release.
5. Confirm the installation links and release downloads work. Enable GitHub Discussions in repository settings if desired. Review [the Discussion draft](discussion-draft.md) and obtain explicit approval before posting it; publication of a Release does not authorize posting messages to other communities.

Use the repository's existing branch policy; do not force-push or replace unrelated work. A draft Release can be prepared for review. Credentials belong in the local credential manager, never in Git remote URLs or release assets.

## Repository structure decision

No further broad regrouping is needed for v0.1.0. `toollery/` contains the reusable retrieval/cache core; `toollery_dsh/` owns the installed CLI, configuration and DSH assets. Optional benchmark drivers, tests, examples and evidence live in their own top-level directories. User state is created beside the user's configuration and excluded from source control.

Keep this repository focused on the DSH distribution. Historical research datasets, generated manuals, run logs and upstream checkouts belong outside the distributable tree or in ignored development directories. A future DSH/npm bundle or another harness can be added when its actual distribution requirements are known.
