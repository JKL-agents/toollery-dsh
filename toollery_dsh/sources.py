"""Compile tool-source configuration without coupling retrieval to a harness."""
from __future__ import annotations

from pathlib import Path
import re
from .configuration import ConfigurationError, runtime_package
from .runtime import require_tool, runtime_entry
from toollery.processes import resolve_executable

def compile_sources(sources, home=None):
    ROOT = Path(home).resolve() if home is not None else Path.cwd()
    if not isinstance(sources, list) or not sources:
        raise ConfigurationError('tool_sources must be a nonempty list; omit it for the demo pool')
    compiled, names = [], set()
    for item in sources:
        if not isinstance(item, dict):
            raise ConfigurationError('Every tool source must be an object')
        name = item.get('id', '')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', name) or name in names:
            raise ConfigurationError('Tool-source ids must be unique MCP-compatible names')
        names.add(name)
        kind = item.get('type')
        if kind == 'filesystem':
            if not isinstance(item.get('root'), str) or not item['root'].strip():
                raise ConfigurationError('Filesystem source root must be a nonempty path')
            directory = (ROOT / item['root']).resolve()
            if not directory.is_dir():
                raise ConfigurationError('Configured filesystem root must already exist')
            node, _ = require_tool('node')
            runtime_entry(ROOT)
            entry = ROOT / '.toollery/web-runtime/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js'
            if not entry.is_file():
                raise ConfigurationError('Filesystem MCP runtime is incomplete. Run toollery-dsh setup-web with the same --config.')
            config = {'transport': 'stdio', 'command': node, 'args': [str(entry), str(directory)]}
        elif kind == 'mcp':
            if not isinstance(item.get('config'), dict):
                raise ConfigurationError('MCP source config must be an object')
            config = dict(item['config'])
            if not isinstance(config.get('transport'), str) or config['transport'] not in {'stdio', 'streamable-http'}:
                raise ConfigurationError('Unsupported MCP transport')
            if config['transport'] == 'stdio':
                if not isinstance(config.get('cwd', '.'), str):
                    raise ConfigurationError('MCP cwd must be a path string')
                cwd = (ROOT / config.get('cwd', '.')).resolve()
                if not cwd.is_dir():
                    raise ConfigurationError('MCP cwd must be an existing directory relative to the config directory')
                args = config.get('args', [])
                if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
                    raise ConfigurationError('MCP args must be an array of strings')
                try:
                    config['command'] = resolve_executable(config.get('command'), cwd)
                except ValueError as error:
                    raise ConfigurationError(str(error)) from error
                config['cwd'] = str(cwd)
            runtime_entry(ROOT)
        elif kind == 'plugin':
            if not isinstance(item.get('path'), str) or not item['path'].strip():
                raise ConfigurationError('Plugin path must be a nonempty string')
            path = (ROOT / item['path']).resolve()
            if not path.is_file():
                raise ConfigurationError('Configured tool plugin does not exist')
            compiled.append({'id': name, 'type': 'plugin', 'module': path.as_uri(),
                             'config': item.get('config', {})})
            continue
        else:
            raise ConfigurationError('Tool-source type must be filesystem, mcp or plugin')
        # References are resolved only inside the host. No secret values in patches.
        if config.get('env') or config.get('headers'):
            raise ConfigurationError('Use env_from/headers_from environment-variable references for MCP credentials')
        config.update(serverName=name, failOnStartupError=True, reconnect={'enabled': False})
        compiled.append({'id': name, 'type': 'mcp', 'config': config,
                         'env_from': item.get('env_from', {}), 'headers_from': item.get('headers_from', {})})
    return compiled


def source_environment(sources, home=None):
    return {'TOOLLERY_DSH_RUNTIME_PACKAGE': str(runtime_package(home or Path.cwd()))} if sources is not None else {}
