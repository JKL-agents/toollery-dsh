"""User-owned configuration, credentials and installed resource locations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlsplit

ASSETS = Path(__file__).resolve().parent / 'assets'
DSH_VERSION = '0.2.1-rc.2'


class ConfigurationError(ValueError):
    """A diagnostic constructed locally, without provider response content."""


def default_config(preset='demo'):
    config = {
        'model': 'deepseek-flash',
        'base_url': 'https://api.deepseek.com/anthropic',
        'api_key_env': 'TOOLLERY_CHAT_API_KEY',
        'max_tokens': 4096,
        'request_timeout_seconds': 180,
        'generator_config': 'manual-generator.json',
        'cache_dir': '.toollery/cache',
        'dsh_home': '.toollery/dsh-cli',
        'web_home': '.toollery/dsh-web',
        'state_dir': '.toollery/ui-state',
        'representation': 'spec_manual', 'budget': 'top_k', 'k': 3,
    }
    if preset == 'filesystem':
        config.update(
            preset_id='toollery-filesystem',
            persona='You are a helpful assistant using real filesystem tools. Use an available tool to inspect or change files when requested. Stay within the configured allowed directory. If the current selection cannot perform an action, explain that limitation. Do not invent tool names.',
            tool_sources=[{'id': 'filesystem', 'type': 'filesystem', 'root': 'playground'}],
        )
    return config


def default_generator():
    return {
        'provider': 'command',
        'command': ['{python}', '-m', 'toollery_dsh.provider_bridge'],
        'model': 'deepseek-flash',
        'parameters': {'api_url': 'https://api.deepseek.com',
                       'api_key_env': 'TOOLLERY_MANUAL_API_KEY', 'temperature': 0,
                       'timeout_seconds': 120, 'retries': 2},
        'bridge_version': 'openai-compatible-v1-paper-a1', 'timeout_seconds': 400,
    }


def initialize(directory, preset='demo'):
    home = Path(directory).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    files = {
        'toollery.json': json.dumps(default_config(preset), indent=2) + '\n',
        'manual-generator.json': json.dumps(default_generator(), indent=2) + '\n',
        '.env.example': '# Shared fallback; or set separate keys below.\nTOOLLERY_API_KEY=\nTOOLLERY_CHAT_API_KEY=\nTOOLLERY_MANUAL_API_KEY=\n',
        '.gitignore': '.env\n.env.*\n!.env.example\n.toollery/\n',
    }
    created, preserved = [], []
    for name, content in files.items():
        path = home / name
        if path.exists():
            preserved.append(name)
        else:
            path.write_text(content, encoding='utf-8')
            created.append(name)
    if preset == 'filesystem':
        target = home / 'playground'
        target.mkdir(exist_ok=True)
        for source in (ASSETS / 'playground').iterdir():
            if source.is_file() and not (target / source.name).exists():
                shutil.copyfile(source, target / source.name)
    return {'directory': str(home), 'created': created, 'preserved': preserved}


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise ConfigurationError(f'Cannot read JSON configuration: {path}') from None
    if not isinstance(value, dict):
        raise ConfigurationError(f'Configuration must be a JSON object: {path}')
    return value


def load_config(path):
    path = Path(path).expanduser().resolve()
    config = {**default_config(), **read_json(path)}
    for name in ('model', 'api_key_env', 'generator_config', 'cache_dir', 'dsh_home', 'web_home', 'state_dir'):
        if not isinstance(config[name], str) or not config[name].strip():
            raise ConfigurationError(f'{name} must be a nonempty string')
    parts = urlsplit(config.get('base_url', ''))
    if (parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment):
        raise ConfigurationError('base_url must be an HTTP(S) URL without embedded credentials, query or fragment')
    for name in ('k', 'max_tokens', 'request_timeout_seconds'):
        if isinstance(config[name], bool) or not isinstance(config[name], int) or config[name] < 1:
            raise ConfigurationError(f'{name} must be a positive integer')
    if config['representation'] not in {'spec', 'manual', 'spec_manual'} or config['budget'] not in {'all', 'top_k'}:
        raise ConfigurationError('Invalid representation or budget')
    return config, path.parent


def generator_config(config, home):
    value = read_json(home / config['generator_config'])
    return resolve_generator(value)


def resolve_generator(value):
    value = dict(value)
    if 'command' in value:
        if not isinstance(value['command'], list) or not value['command'] or any(not isinstance(part, str) or not part for part in value['command']):
            raise ConfigurationError('Generator command must be a nonempty array of strings')
        value['command'] = [sys.executable if part == '{python}' else part for part in value['command']]
    return value


def configured_generator(value, home, *, require_executable=True):
    from toollery.generation import make_generator
    from .runtime import child_command
    try:
        generator = make_generator(value, cwd=home)
        if require_executable and value.get('provider') == 'command':
            child_command(value['command'], cwd=home)
        return generator
    except ValueError as error:
        raise ConfigurationError(str(error)) from error


def load_generator_credentials(value, home):
    # Custom/local generators need not use an API or this project's key layout.
    parameters = value.get('parameters', {})
    if not isinstance(parameters, dict):
        raise ConfigurationError('Generator parameters must be an object')
    name = parameters.get('api_key_env')
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError('Generator api_key_env must be a nonempty string')
        load_key(name, home)


def load_environment(home, names):
    """Import only requested variables; no shell evaluation or interpolation."""
    path = Path(home) / '.env'
    if not path.is_file():
        return
    names = set(names)
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        key, separator, value = line.partition('=')
        key = key.strip().removeprefix('export ').strip()
        if separator and key in names and not os.environ.get(key):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ[key] = value


def load_key(variable, home=None, *, required=True):
    home = Path.cwd() if home is None else Path(home)
    load_environment(home, [variable, 'TOOLLERY_API_KEY'])
    value = os.environ.get(variable)
    if not value and variable in {'TOOLLERY_CHAT_API_KEY', 'TOOLLERY_MANUAL_API_KEY'}:
        value = os.environ.get('TOOLLERY_API_KEY')
        if value:
            os.environ[variable] = value
    if not value and required:
        raise ConfigurationError(f'Set {variable} in the environment or {home / ".env"}. The shared TOOLLERY_API_KEY also works for the default key names.')
    return value


def load_source_credentials(config, home):
    names = []
    sources = config.get('tool_sources', [])
    if not isinstance(sources, list):
        raise ConfigurationError('tool_sources must be a list')
    for source in sources:
        if not isinstance(source, dict):
            raise ConfigurationError('Every tool source must be an object')
        for field in ('env_from', 'headers_from'):
            refs = source.get(field, {})
            if not isinstance(refs, dict) or any(not isinstance(key, str) or not isinstance(name, str) or not name.strip() for key, name in refs.items()):
                raise ConfigurationError(f'{field} must map names to nonempty environment variable names')
            names.extend(refs.values())
    load_environment(home, names)
    for name in names:
        if not os.environ.get(name):
            raise ConfigurationError(f'Missing tool-source environment variable {name}. Set it in the environment or the config directory .env.')


def runtime_package(home):
    return Path(home) / '.toollery/web-runtime/node_modules/@deepseek-ai/dsh/package.json'
