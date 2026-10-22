"""Explicit executable resolution and Windows npm-shim invocation."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


def resolve_executable(name, cwd=None):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Executable must be a nonempty string')
    home = Path(cwd).resolve() if cwd is not None else Path.cwd()
    path = Path(name)
    if path.is_absolute() or '/' in name or '\\' in name:
        candidate = (home / path).resolve()
        executable = shutil.which(str(candidate))
    else:
        # Bare names use PATH, never an accidental executable in the caller's cwd.
        executable = None
        for directory in os.environ.get('PATH', '').split(os.pathsep):
            if directory.strip('"'):
                candidate = (home / directory.strip('"') / name).resolve()
                executable = shutil.which(str(candidate))
                if executable:
                    break
    if not executable:
        raise ValueError(f'Executable {name!r} was not found. Install it and reopen the terminal, or configure an explicit executable path.')
    return str(Path(executable).resolve())


def child_command(launch, cwd=None):
    if not isinstance(launch, list) or not launch or any(not isinstance(arg, str) for arg in launch):
        raise ValueError('Command must be a nonempty array of strings')
    launch = [resolve_executable(launch[0], cwd), *launch[1:]]
    if os.name != 'nt' or Path(launch[0]).suffix.lower() not in {'.cmd', '.bat'}:
        return launch
    if any(any(char in arg for char in '\"%\r\n') for arg in launch):
        raise ValueError('Windows batch arguments cannot contain quotes, percent signs or newlines. Invoke the underlying executable (for example node) directly.')
    def quote(arg):
        trailing = len(arg) - len(arg.rstrip('\\'))
        return '"' + arg + '\\' * trailing + '"'
    command = ' '.join(quote(arg) for arg in launch)
    interpreter = os.environ.get('COMSPEC') or shutil.which('cmd.exe') or 'cmd.exe'
    return subprocess.list2cmdline([interpreter]) + ' /d /s /v:off /c "' + command + '"'
