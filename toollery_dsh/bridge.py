"""Run an existing DSH command as a child of the authenticated Python bridge."""
from __future__ import annotations

import os
import subprocess
import threading

from toollery.adapters.dsh import DSHBridge
from toollery.manuals import ManualCache
from .configuration import (ASSETS, ConfigurationError, generator_config, load_config,
                            configured_generator, load_generator_credentials, load_key, load_source_credentials)
from .runtime import child_command
from .sources import compile_sources, source_environment
from toollery.storage import atomic_json


def run(args, output):
    launch = args.launch[1:] if args.launch and args.launch[0] == '--' else args.launch
    if not launch:
        raise ConfigurationError('Use toollery-dsh bridge --config toollery.json -- dsh <arguments>.')
    config, home = load_config(args.config)
    command = child_command([*launch, '--patch', str(output / 'bridge.patch.json')], cwd=home)
    for name, field in [('mode', 'representation'), ('budget', 'budget'), ('k', 'k')]:
        if getattr(args, name) is not None:
            config[field] = getattr(args, name)
    load_source_credentials(config, home)
    load_key(config['api_key_env'], home, required=False)
    generation = generator_config(config, home)
    if args.generate_manuals:
        load_generator_credentials(generation, home)
    generator = configured_generator(generation, home, require_executable=args.generate_manuals)
    cache, lock = ManualCache(home / config['cache_dir']), threading.Lock()

    def prepare(bridge, session_id):
        with lock:
            if not bridge.sessions[session_id].ready:
                report = bridge.prepare(session_id, generator, cache, workers=args.workers,
                                        cache_only=not args.generate_manuals)
                print(f"Ready: {report['valid']}/{report['total']} manuals ({report['cache_hits']} cached).", flush=True)

    # Existing integrations keep their provider, persona and tools. Native
    # presentation and complete-current-list updates are the host's contract.
    patch = output / 'bridge.patch.json'
    plugin = {'id': 'toollery', 'name': (ASSETS / 'dsh/toollery.mjs').as_uri()}
    if config.get('tool_sources') is not None:
        plugin['config'] = {'toolSources': compile_sources(config['tool_sources'], home)}
    atomic_json(patch, [{'id': 'tools', 'config': {'mode': 'native'}}, {'insert': [plugin]}])
    with DSHBridge(output, representation=config['representation'], budget=config['budget'], k=config['k'],
                   enabled=not args.toollery_off, on_snapshot=prepare,
                   state_dir=home / config['state_dir']) as bridge:
        env = {**os.environ, **source_environment(config.get('tool_sources'), home),
               'TOOLLERY_BRIDGE_URL': bridge.url, 'TOOLLERY_BRIDGE_TOKEN': bridge.token}
        print(f'Toollery bridge running. Patch: {patch}\nReports: {output}', flush=True)
        # Token stays in the child environment, never in command arguments.
        process = subprocess.Popen(command, cwd=home, env=env,
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
