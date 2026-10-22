"""Run an existing DSH command as a child of the authenticated Python bridge."""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import threading

from toollery.adapters.dsh import DSHBridge
from toollery.generation import make_generator
from toollery.manuals import ManualCache
from .configuration import (ASSETS, ConfigurationError, generator_config, load_config,
                            load_key, load_source_credentials)
from .sources import compile_sources, source_environment
from toollery.storage import atomic_json


def child_command(launch):
    executable = shutil.which(launch[0])
    launch = [executable or launch[0], *launch[1:]]
    if os.name != 'nt' or Path(launch[0]).suffix.lower() not in {'.cmd', '.bat'}:
        return launch
    # npm-style shims need cmd.exe. /d prevents a machine's Command Processor
    # AutoRun hook from changing the configured working directory. Keep shell
    # expansion out of arguments; complex commands can invoke node directly.
    if any(any(char in arg for char in '\"%\r\n') for arg in launch):
        raise ConfigurationError('Windows batch arguments cannot contain quotes, percent signs or newlines. Invoke the underlying executable (for example node) directly.')
    def quote(arg):
        trailing = len(arg) - len(arg.rstrip('\\'))
        return '\"' + arg + '\\' * trailing + '\"'
    command = ' '.join(quote(arg) for arg in launch)
    interpreter = os.environ.get('COMSPEC') or shutil.which('cmd.exe') or 'cmd.exe'
    return subprocess.list2cmdline([interpreter]) + ' /d /s /v:off /c \"' + command + '\"'


def run(args, output):
    launch = args.launch[1:] if args.launch and args.launch[0] == '--' else args.launch
    if not launch:
        raise ConfigurationError('Use toollery-dsh bridge --config toollery.json -- dsh <arguments>.')
    config, home = load_config(args.config)
    for name, field in [('mode', 'representation'), ('budget', 'budget'), ('k', 'k')]:
        if getattr(args, name) is not None:
            config[field] = getattr(args, name)
    load_source_credentials(config, home)
    load_key(config['api_key_env'], home, required=False)
    generation = generator_config(config, home)
    if args.generate_manuals:
        load_key(generation['parameters']['api_key_env'], home)
    generator, cache, lock = make_generator(generation), ManualCache(home / config['cache_dir']), threading.Lock()

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
    command = child_command([*launch, '--patch', str(patch)])
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
