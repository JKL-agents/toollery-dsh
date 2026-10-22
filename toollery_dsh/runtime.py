"""Bounded executable preflight checks; no model calls or private fallbacks."""
from __future__ import annotations

import json
import re
import subprocess

from toollery.processes import child_command as _child_command
from .configuration import ConfigurationError, DSH_VERSION, runtime_package


def child_command(launch, cwd=None):
    try:
        return _child_command(launch, cwd)
    except ValueError as error:
        raise ConfigurationError(str(error)) from error


def require_tool(name):
    requirement = {'node': 'Node.js 22.19+ on 22.x, or 24+', 'pnpm': 'pnpm 11.x'}[name]
    hint = f'Install {requirement}, reopen the terminal, and verify `{name} --version` in that same terminal.'
    try:
        command = child_command([name, '--version'])
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=10, check=False)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise ConfigurationError(f'Cannot run {name}. {hint}') from error
    version = result.stdout.strip()
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', version)
    numbers = tuple(map(int, match.groups())) if match else ()
    supported = bool(numbers) and ((numbers[0] >= 24 or (numbers[0] == 22 and numbers >= (22, 19, 0)))
                                  if name == 'node' else numbers[0] == 11)
    if result.returncode or not supported:
        # Do not reflect arbitrary executable stdout/stderr in diagnostics.
        raise ConfigurationError(f'{name} is incompatible or its version check failed. {hint}')
    return command[0] if isinstance(command, list) else name, version


def runtime_entry(home):
    package = runtime_package(home).parent
    try:
        metadata = json.loads((package / 'package.json').read_text(encoding='utf-8'))
        if metadata['version'] != DSH_VERSION:
            raise ValueError('version')
        entry = metadata['bin']
        entry = entry if isinstance(entry, str) else entry['dsh']
        path = (package / entry).resolve()
        if not path.is_relative_to(package.resolve()) or not path.is_file():
            raise ValueError('entry')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ConfigurationError(f'DSH Web runtime is missing, incomplete or incompatible (requires {DSH_VERSION}). Run toollery-dsh setup-web with the same --config.') from error
    return path, metadata['version']


def web_runtime(home):
    node, _ = require_tool('node')
    entry, version = runtime_entry(home)
    return [node, str(entry)], version
