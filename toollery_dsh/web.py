"""Launch the pinned official DSH Web UI with the Toollery utility preset."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

from .configuration import (ASSETS, ConfigurationError, load_config, generator_config as read_generator,
                            configured_generator, load_generator_credentials, load_key, load_source_credentials)
from .runtime import web_runtime as _web_runtime
from .chat import write_patch
from .sources import source_environment
from toollery.adapters.dsh import DSHBridge
from toollery.generation import MockGenerator
from toollery.manuals import ManualCache
from toollery.storage import atomic_json, digest


def write_web_patch(path, config, base_url, home=None):
    write_patch(path, config['model'], config.get('tool_sources'), home)
    rows = json.loads(path.read_text(encoding='utf-8'))
    provider = next(row for row in rows if row.get('id') == 'llm-deepseek')['config']
    provider.update(baseURL=base_url, apiKeyEnv=config['api_key_env'],
                    maxTokens=config['max_tokens'], thinking='disabled')
    rows.extend({'id': name, 'disabled': True} for name in (
        'preset-minimal', 'preset-standard', 'preset-cordis', 'preset-ptc',
        'session-title-llm', 'hmr', 'office-to-pdf', 'directory-picker',
    ))
    rows.extend([
        {'id': 'agent-default-model', 'config': {'provider': 'deepseek-official', 'model': config['model']}},
        {'id': 'agent-preset-registry', 'config': {'default': config.get('preset_id', 'toollery-utilities')}},
        {'insert': [
            {'id': 'toollery-directory-browser', 'name': '@deepseek-ai/dsh-host-directory-picker-browse'},
            {'id': 'toollery-directory-browser-ui', 'name': '@deepseek-ai/dsh-client-ui-directory-picker-browse'},
            {'id': 'preset-toollery', 'name': '@deepseek-ai/dsh-agent-preset', 'config': {
                'id': config.get('preset_id', 'toollery-utilities'), 'order': 0, 'plugins': [
                    {'id': 'persona', 'name': '@deepseek-ai/dsh-persona', 'config': {
                        'prefix': config.get('persona', 'You are a helpful assistant using local utility tools. Use a matching available tool for calculations and transformations. If none matches, say so. Do not invent tool names. Respond in the user\'s language.'),
                        'complete': True, 'includeRuntimeContext': True,
                    }},
                ],
            }},
            {'id': 'toollery-web-controls', 'name': (ASSETS / 'dsh/web-controls.mjs').as_uri()},
            {'id': 'toollery-ui', 'name': (ASSETS / 'dsh/ui/index.js').as_uri()},
        ]},
    ])
    if config.get('_launcher_workspace'):
        rows.append({'insert': [{'id': 'toollery-launcher-ui',
                    'name': (ASSETS / 'dsh/launcher-ui/index.js').as_uri()}]})
    atomic_json(path, rows)


def web_runtime(home=None):
    return _web_runtime(home or Path.cwd())


def run(args, output):
    config, ROOT = load_config(args.config)
    launch, version = web_runtime(ROOT)
    load_source_credentials(config, ROOT)
    for arg, field in [('mode', 'representation'), ('budget', 'budget'), ('k', 'k')]:
        if getattr(args, arg) is not None:
            config[field] = getattr(args, arg)
    # Report a port conflict before spawning a process or making model calls.
    try:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', args.port))
    except OSError as error:
        raise ConfigurationError(f'Cannot bind Web port {args.port}. Stop the existing server or choose another --port; check localhost permissions.') from error
    generator_config = read_generator(config, ROOT)
    generator = MockGenerator() if args.offline else configured_generator(
        generator_config, ROOT, require_executable=args.generate_manuals)
    key = 'local-mock-key' if args.offline else load_key(config['api_key_env'], ROOT)
    if args.generate_manuals and not args.offline:
        load_generator_credentials(generator_config, ROOT)
    cache_path = config['cache_dir']
    if args.offline:
        cache_path = cache_path + '-web-mock' if config.get('tool_sources') else '.toollery/cache-web-mock'
    cache = ManualCache(ROOT / cache_path)
    state_path = config.get('state_dir', 'work/dsh-ui-state') + ('-mock' if args.offline else '')
    home_path = config.get('web_home', 'work/dsh-home-web') + ('-mock' if args.offline else '')
    preparation_lock = threading.Lock()

    def prepare(bridge, session_id):
        # Multiple browser sessions can arrive concurrently. Serial preparation
        # avoids duplicate work; ManualCache also retains its per-entry locks.
        with preparation_lock:
            if bridge.sessions[session_id].ready:
                return
            print(f'Preparing session {session_id}: {len(bridge.sessions[session_id].pool.tools)} tools', flush=True)
            try:
                report = bridge.prepare(session_id, generator, cache, workers=args.workers,
                                        cache_only=not args.generate_manuals and not args.offline)
            except Exception as error:
                print(f'Session not ready ({type(error).__name__}). Check manual-preparation.json; use --generate-manuals to fill missing cache entries.', flush=True)
                raise
            atomic_json(output / ('preparation-' + digest(session_id).split(':')[-1][:20] + '.json'), report)
            print(f"Ready: {report['valid']}/{report['total']} manuals; {report['cache_hits']} cache hits; {report['generated']} generated.", flush=True)

    def selection(selected):
        mode = f"{selected['representation']}, {selected['budget']}" if selected['enabled'] else 'OFF, full pool'
        print(f"Turn {selected['turn']} ({mode}): "
              + (', '.join(selected['selected_names']) or '(none)'), flush=True)

    with ExitStack() as stack:
        mock = None
        if args.offline:
            from .mock import MockMessages
            mock = stack.enter_context(MockMessages())
        bridge = stack.enter_context(DSHBridge(output, representation=config['representation'],
                     budget=config['budget'], k=config['k'], on_snapshot=prepare, on_selection=selection,
                     enabled=not args.toollery_off,
                     state_dir=ROOT / state_path))
        patch = output / 'cordis.patch.json'
        write_web_patch(patch, config, mock.url if mock else config['base_url'], ROOT)
        env = os.environ.copy()
        env.update({config['api_key_env']: key, 'DSH_HOME': str(ROOT / home_path),
                    'TOOLLERY_BRIDGE_URL': bridge.url, 'TOOLLERY_BRIDGE_TOKEN': bridge.token,
                    'DSH_TOOLS_MODE': 'native', 'DSH_TELEMETRY_DISABLED': '1'})
        env.update(source_environment(config.get('tool_sources'), ROOT))
        if config.get('_launcher_workspace'):
            env.update(TOOLLERY_LAUNCHER_WORKSPACE=config['_launcher_workspace'],
                       TOOLLERY_LAUNCHER_PRESET=config.get('preset_id', 'toollery-utilities'),
                       TOOLLERY_LAUNCHER_ID=output.name)
        command = [*launch, '--profile', 'web', '--patch', str(patch), '--host', '127.0.0.1', '--port', str(args.port)]
        if args.no_open:
            command.append('--no-open')
        atomic_json(output / 'runtime.json', {
            'runtime_version': version, 'profile': 'web', 'preset': config.get('preset_id', 'toollery-utilities'),
            'url': f'http://127.0.0.1:{args.port}', 'offline_mock': bool(mock),
            'manual_generation_enabled': args.generate_manuals, 'workers': args.workers,
            'toollery_enabled': not args.toollery_off,
            'tool_scope_contract': 'per_user_turn_v1', 'model': config['model'],
            'tool_source_ids': [item['id'] for item in config.get('tool_sources', [])],
        })
        print('Starting official DSH Web + Toollery. Open the authenticated `dsh web:` URL printed below (including ?token=...).\n'
              f'Reports: {output}\nSelect a workspace, then use /toollery status or send a prompt. Ctrl+C stops the server.', flush=True)
        process = subprocess.Popen(command, cwd=ROOT, env=env,
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        try:
            return process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if mock:
                atomic_json(output / 'mock-requests.json', mock.requests)
