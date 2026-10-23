"""Local manual-provider settings. Only a redacted view crosses the UI boundary."""
from __future__ import annotations

import os
from pathlib import Path

from toollery.storage import atomic_json
from .configuration import ConfigurationError, default_generator, read_json
from .provider_bridge import _api_url, BridgeError


def builtin(value):
    return (value.get('provider') == 'command'
            and value.get('command') == ['{python}', '-m', 'toollery_dsh.provider_bridge'])


def manual_key(home, value):
    path = Path(home) / 'manual-credentials.json'
    if path.is_file():
        key = read_json(path).get('api_key')
        if isinstance(key, str) and key.strip():
            return key.strip()
    parameters = value.get('parameters', {})
    variable = parameters.get('api_key_env', 'TOOLLERY_MANUAL_API_KEY')
    names = [variable]
    if variable == 'TOOLLERY_MANUAL_API_KEY':
        names.append('TOOLLERY_API_KEY')
    # Do not mutate worker-global os.environ: editing a saved key must take
    # effect in the next generation subprocess without restarting Desktop.
    file_values = {}
    path = Path(home) / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key, sep, secret = line.partition('=')
            key = key.strip().removeprefix('export ').strip()
            if sep and key in names:
                secret = secret.strip()
                if len(secret) >= 2 and secret[0] == secret[-1] and secret[0] in {'"', "'"}:
                    secret = secret[1:-1]
                file_values[key] = secret
    for name in names:
        key = os.environ.get(name) or file_values.get(name)
        if key and key.strip():
            return key.strip()
    return None


def provider_view(home):
    value = read_json(Path(home) / 'manual-generator.json')
    supported = builtin(value)
    url = ''
    if supported:
        try:
            _api_url(value.get('parameters', {}))
            url = value.get('parameters', {}).get('api_url', 'https://api.deepseek.com')
        except (BridgeError, ValueError):
            pass
    return {'editable': supported, 'model': value.get('model', '') if supported else '',
            'api_url': url, 'key_configured': bool(manual_key(home, value)) if supported else False,
            'configured': bool(supported and url and value.get('model') and manual_key(home, value)),
            'protocol': 'OpenAI Chat Completions', 'proxy_query_count': 3}


def save_provider(home, settings):
    if (not isinstance(settings, dict)
            or set(settings) - {'model', 'api_url', 'api_key'}):
        raise ConfigurationError('Provide only model, api_url and optionally api_key.')
    model, url, key = (settings.get(name) for name in ('model', 'api_url', 'api_key'))
    if not isinstance(model, str) or not model.strip() or len(model) > 200 or any(ord(c) < 32 for c in model):
        raise ConfigurationError('Enter a nonempty model identifier (at most 200 characters).')
    if not isinstance(url, str) or not url.strip() or len(url) > 2000:
        raise ConfigurationError('Enter the manual-generation API base URL.')
    try:
        _api_url({'api_url': url.strip()})
    except (BridgeError, ValueError):
        raise ConfigurationError('Use an HTTPS OpenAI-compatible URL without credentials, query or fragment. HTTP is allowed only for localhost.') from None
    if key is not None and (not isinstance(key, str) or len(key) > 4096 or any(ord(c) < 32 for c in key)):
        raise ConfigurationError('Enter a valid API key without control characters.')
    old = read_json(Path(home) / 'manual-generator.json')
    if not (key and key.strip()) and not manual_key(home, old):
        raise ConfigurationError('Enter a manual-generation API key. Blank retains an existing saved/environment key.')
    value = default_generator()
    value['model'] = model.strip()
    value['parameters']['api_url'] = url.strip().rstrip('/')
    if not (key and key.strip()):
        value['parameters']['api_key_env'] = old.get('parameters', {}).get('api_key_env', 'TOOLLERY_MANUAL_API_KEY')
    if key and key.strip():
        # mkstemp/atomic replace uses user-only permissions on Unix. Windows
        # inherits the user's DSH profile ACL; this is not an OS keychain.
        atomic_json(Path(home) / 'manual-credentials.json', {'api_key': key.strip()})
    atomic_json(Path(home) / 'manual-generator.json', value)
    return provider_view(home)
