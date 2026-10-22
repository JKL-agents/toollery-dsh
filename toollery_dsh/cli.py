"""Installable entry point. User state is always relative to the config file."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from . import __version__
from .configuration import (ASSETS, ConfigurationError, DSH_VERSION, initialize,
                            load_config, load_key, runtime_package)


def setup_web(config_path):
    _, home = load_config(config_path)
    pnpm = shutil.which('pnpm')
    if not pnpm:
        raise ConfigurationError('Install pnpm 11 first, then run toollery-dsh setup-web again.')
    target = home / '.toollery/web-runtime'
    target.mkdir(parents=True, exist_ok=True)
    for name in ('package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml'):
        shutil.copyfile(ASSETS / 'dsh/web-runtime' / name, target / name)
    return subprocess.call([pnpm, '--dir', str(target), 'install', '--frozen-lockfile', '--ignore-scripts'])


def doctor(config_path, require_web=False):
    config, home = load_config(config_path)
    checks = []
    def add(name, ok, detail, required=True):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail, 'required': required})
    add('python', sys.version_info >= (3, 12), sys.version.split()[0])
    for package in ('deepseek-harness-sdk', 'deepseek-harness-runtime-bin'):
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = 'not installed'
        add(package, version == '0.2.1rc2', version)
    node = shutil.which('node')
    version = subprocess.check_output([node, '--version'], text=True).strip() if node else ''
    numbers = re.match(r'v(\d+)\.(\d+)\.(\d+)', version)
    supported = numbers and (tuple(map(int, numbers.groups())) >= (24, 0, 0)
                             or (int(numbers[1]) == 22 and tuple(map(int, numbers.groups())) >= (22, 19, 0)))
    add('node', supported, version or 'not installed', required=require_web or bool(config.get('tool_sources')))
    add('pnpm', shutil.which('pnpm') is not None, 'found' if shutil.which('pnpm') else 'not installed', required=require_web)
    required_assets = ['demo-tools.mjs', 'dsh/toollery.mjs', 'dsh/sources.mjs',
                       'dsh/ui/client.js', 'dsh/ui/index.js', 'dsh/ui/package.json',
                       'dsh/web-controls.mjs', 'dsh/web-runtime/pnpm-lock.yaml']
    missing = [name for name in required_assets if not (ASSETS / name).is_file()]
    add('installed assets', not missing, ', '.join(missing) or 'complete')
    add('manual config', (home / config['generator_config']).is_file(), config['generator_config'])
    package = runtime_package(home)
    try:
        web_version = json.loads(package.read_text(encoding='utf-8'))['version']
    except (OSError, ValueError, KeyError):
        web_version = 'not installed; run toollery-dsh setup-web'
    add('web runtime', web_version == DSH_VERSION, web_version, required=require_web)
    add('chat credential', bool(load_key(config['api_key_env'], home, required=False)),
        'configured' if load_key(config['api_key_env'], home, required=False) else 'not configured (offline checks still work)', required=False)
    return {'ready': all(item['ok'] for item in checks if item['required']),
            'config_directory': str(home), 'checks': checks, 'model_requests': 0}


def parser():
    root = argparse.ArgumentParser(description='Toollery for DeepSeek Harness')
    root.add_argument('--version', action='version', version=__version__)
    sub = root.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Create local templates without overwriting existing files')
    init.add_argument('directory', nargs='?', default='.')
    init.add_argument('--preset', choices=['demo', 'filesystem'], default='demo')
    for name in ('doctor', 'setup-web', 'prepare', 'chat', 'web', 'bridge'):
        p = sub.add_parser(name)
        p.add_argument('--config', default='toollery.json', type=Path)
        if name == 'doctor':
            p.add_argument('--require-web', action='store_true')
            continue
        if name == 'setup-web':
            continue
        p.add_argument('--workers', type=int, default=8)
        p.add_argument('--mode', choices=['spec', 'manual', 'spec_manual'])
        p.add_argument('--budget', choices=['all', 'top_k'])
        p.add_argument('--k', type=int)
        p.add_argument('--output', type=Path)
        p.add_argument('--cache-only', action='store_true')
        if name == 'prepare':
            p.add_argument('--offline', action='store_true', help='Use mock manuals only; isolated from real manual cache')
            continue
        p.add_argument('--generate-manuals', action='store_true', help='Allow model calls for missing manuals; startup is otherwise cache-only')
        p.add_argument('--toollery-off', action='store_true', help='Provide all tool specs; skip retrieval/manual preparation')
        if name == 'web':
            p.add_argument('--port', type=int, default=3080)
            p.add_argument('--no-open', action='store_true')
            p.add_argument('--offline', action='store_true', help='Scripted demo model and mock manuals; no API calls')
        elif name == 'chat':
            p.add_argument('--offline-smoke', action='store_true', help='Six scripted demo turns on real DSH; no API calls')
            p.add_argument('--spec-only', action='store_true', help='Build only the spec index, without manuals')
            p.add_argument('--prompt', action='append')
        else:
            p.add_argument('launch', nargs=argparse.REMAINDER, help='DSH executable and arguments, after --')
    return root


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    p = parser()
    args = p.parse_args(argv)
    output = None
    try:
        if args.command == 'init':
            print(json.dumps(initialize(args.directory, args.preset), indent=2))
            print('Copy .env.example to .env, fill API credentials, then run toollery-dsh prepare.')
            return 0
        if args.command == 'doctor':
            report = doctor(args.config, args.require_web)
            print(json.dumps(report, indent=2))
            return 0 if report['ready'] else 1
        if args.command == 'setup-web':
            return setup_web(args.config)
        if not 1 <= args.workers <= 8 or (args.k is not None and args.k < 1):
            p.error('workers must be 1..8 and k must be positive')
        if args.cache_only and getattr(args, 'generate_manuals', False):
            p.error('--cache-only and --generate-manuals are mutually exclusive')
        config, home = load_config(args.config)
        if args.command == 'web' and not 1 <= args.port <= 65535:
            p.error('port must be 1..65535')
        if args.command == 'chat' and args.offline_smoke:
            if args.spec_only or args.prompt or args.toollery_off or config.get('tool_sources'):
                p.error('--offline-smoke uses the built-in demo pool and its own fixture turns')
        output = (home / args.output).resolve() if args.output else home / '.toollery/runs' / (args.command + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        output.mkdir(parents=True, exist_ok=False)
        if args.command == 'web':
            from .web import run
            return run(args, output)
        if args.command == 'bridge':
            from .bridge import run
            return run(args, output)
        from .chat import run
        args.prepare_only = args.command == 'prepare'
        if args.prepare_only:
            args.offline_smoke = args.offline
            args.spec_only, args.toollery_off, args.prompt = False, False, None
        else:
            args.cache_only = not args.generate_manuals and not args.offline_smoke
        print(json.dumps(run(args, output), indent=2))
        return 0
    except KeyboardInterrupt:
        print('Stopped.' + (f' Reports: {output}' if output else ''))
        return 130
    except ConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        if output:
            from toollery.storage import atomic_json
            try:
                atomic_json(output / 'failure.json', {'error_type': type(error).__name__})
            except OSError:
                pass
        print(f'Failed ({type(error).__name__}).' + (f' Reports: {output}' if output else '')
              + ' For missing manuals, run toollery-dsh prepare; check doctor for installation/configuration issues.', file=sys.stderr)
        return 1
