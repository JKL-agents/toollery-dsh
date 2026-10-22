"""Compile tool-source configuration without coupling retrieval to a harness."""
from __future__ import annotations

from pathlib import Path
import re
import shutil

from .configuration import runtime_package

def compile_sources(sources, home=None):
    ROOT = Path(home).resolve() if home is not None else Path.cwd()
    if not isinstance(sources, list) or not sources:
        raise ValueError('tool_sources must be a nonempty list; omit it for the demo pool')
    node = shutil.which('node')
    compiled, names = [], set()
    for item in sources:
        if not isinstance(item, dict):
            raise ValueError('Every tool source must be an object')
        name = item.get('id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', name) or name in names:
            raise ValueError('Tool-source ids must be unique MCP-compatible names')
        names.add(name)
        kind = item.get('type')
        if kind == 'filesystem':
            directory = (ROOT / item['root']).resolve()
            if not directory.is_dir():
                raise ValueError('Configured filesystem root must already exist')
            entry = ROOT / '.toollery/web-runtime/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js'
            if not node or not entry.is_file():
                raise RuntimeError('Install the pinned Web runtime and Node.js first')
            config = {'transport': 'stdio', 'command': node, 'args': [str(entry), str(directory)]}
        elif kind == 'mcp':
            config = dict(item['config'])
            if config.get('transport') not in {'stdio', 'streamable-http'}:
                raise ValueError('Unsupported MCP transport')
            if config['transport'] == 'stdio':
                config['cwd'] = str((ROOT / config.get('cwd', '.')).resolve())
        elif kind == 'plugin':
            path = (ROOT / item['path']).resolve()
            if not path.is_file():
                raise ValueError('Configured tool plugin does not exist')
            compiled.append({'id': name, 'type': 'plugin', 'module': path.as_uri(),
                             'config': item.get('config', {})})
            continue
        else:
            raise ValueError('Tool-source type must be filesystem, mcp or plugin')
        # References are resolved only inside the host. No secret values in patches.
        if config.get('env') or config.get('headers'):
            raise ValueError('Use env_from/headers_from environment-variable references for MCP credentials')
        config.update(serverName=name, failOnStartupError=True, reconnect={'enabled': False})
        compiled.append({'id': name, 'type': 'mcp', 'config': config,
                         'env_from': item.get('env_from', {}), 'headers_from': item.get('headers_from', {})})
    return compiled


def source_environment(sources, home=None):
    return {'TOOLLERY_DSH_RUNTIME_PACKAGE': str(runtime_package(home or Path.cwd()))} if sources is not None else {}
