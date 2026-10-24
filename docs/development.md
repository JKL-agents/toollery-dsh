# Development

Run these commands from the repository root. For user installation, see the [plugin guide](plugin.md).

## Layout

```text
toollery/             Preparation, cache, retrieval and metrics
  adapters/dsh.py     Authenticated localhost bridge
toollery_dsh/         Installable CLI, configuration and launchers
  assets/            Cordis plugins, UI and pinned runtime manifests
plugin/              Existing-profile plugin, worker lifecycle and bundle metadata
examples/            Source-checkout configuration examples
scripts/             Optional benchmark and verification drivers
tests/               Offline unit, transport and plugin tests
docs/                User guides, experiment evidence and release drafts
```

## Build and test

The commands below are the full local check/build reference, not a requirement to rerun every suite for every edit. Choose checks relevant to the change; documentation-only edits need link and formatting checks.

```powershell
& .venv/Scripts/python.exe -m pip install -e '.[dev,demo]'
& .venv/Scripts/python.exe -m unittest discover -s tests -v
node --test tests/test_dsh_snapshot.mjs tests/test_dsh_sources.mjs tests/test_dsh_ui.mjs tests/test_dsh_launcher.mjs tests/test_dsh_lifecycle.mjs tests/test_plugin.mjs
& .venv/Scripts/python.exe -m build
& .venv/Scripts/python.exe scripts/build_plugin.py
```

The bundle embeds the worker wheel and shared UI/retrieval assets; the worker itself has no third-party Python dependencies. Only the standalone demo and its DSH acceptance drivers require the Python `demo` extra. Optional experiments need `.[benchmark,demo]` and separately downloaded data. Dataset dumps, benchmark manuals, local credentials, environments and historical run logs are not distributed.

For a plugin-only build, use `python scripts/build_plugin.py --build-wheel` to rebuild the embedded worker first. Reusing a wheel whose Python source differs from the checkout is rejected. After replacing a local test bundle, restart DSH; automatic setup installs the matching worker when possible, while `/toollery setup` is the explicit retry for a same-version local replacement.
