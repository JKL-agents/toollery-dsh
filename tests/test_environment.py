"""Regression checks for user shells rather than the developer's PATH/cwd."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from toollery.data import ToolRecord
from toollery.evidence import normalize
from toollery_dsh import cli
from toollery_dsh.configuration import (ConfigurationError, configured_generator,
                                        initialize, load_generator_credentials, load_source_credentials)
from toollery_dsh.runtime import child_command, require_tool, runtime_entry
from toollery_dsh.sources import compile_sources
from toollery_dsh.web import run as web_run


class EnvironmentTests(unittest.TestCase):
    def test_cli_reports_missing_node_in_real_empty_path_child(self):
        with tempfile.TemporaryDirectory(prefix='toollery user ') as folder:
            home = Path(folder)
            initialize(home)
            code = 'from toollery_dsh.cli import main; import sys; sys.exit(main(sys.argv[1:]))'
            # Absolute interpreter; caller cwd differs from the config directory.
            env = {**os.environ, 'PATH': '', 'TOOLLERY_API_KEY': 'sentinel-secret-never-print'}
            result = subprocess.run([sys.executable, '-c', code, 'web', '--config', str(home / 'toollery.json'), '--offline'],
                                    cwd=Path(__file__).resolve().parents[1], env=env,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Cannot run node', result.stderr)
            self.assertIn('reopen the terminal', result.stderr)
            self.assertNotIn('sentinel-secret', result.stdout + result.stderr)

    def test_versions_broken_executables_and_timeouts(self):
        for name, version, accepted in [('node', 'v22.18.0', False), ('node', 'v22.19.0', True),
                                         ('node', 'v23.1.0', False), ('node', 'v24.0.0', True),
                                         ('pnpm', '10.0.0', False), ('pnpm', '11.25.0', True),
                                         ('node', 'arbitrary private output', False)]:
            with self.subTest(name=name, version=version), patch('toollery_dsh.runtime.child_command', return_value=[name]), \
                    patch('toollery_dsh.runtime.subprocess.run', return_value=subprocess.CompletedProcess([], 0, version)):
                if accepted:
                    self.assertEqual(require_tool(name)[1], version)
                else:
                    with self.assertRaises(ConfigurationError) as caught:
                        require_tool(name)
                    self.assertNotIn('private output', str(caught.exception))
        for error in (PermissionError('private data'), subprocess.TimeoutExpired('node', 10)):
            with self.subTest(error=type(error).__name__), patch('toollery_dsh.runtime.subprocess.run', side_effect=error), \
                    patch('toollery_dsh.runtime.child_command', return_value=['node']):
                with self.assertRaisesRegex(ConfigurationError, 'Cannot run node'):
                    require_tool('node')

    def test_runtime_metadata_and_entry_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            package = home / '.toollery/web-runtime/node_modules/@deepseek-ai/dsh'
            package.mkdir(parents=True)
            metadata = package / 'package.json'
            for content in ('{broken', '{}', '{"version":"old"}',
                            '{"version":"0.2.1-rc.2","bin":"missing.js"}'):
                metadata.write_text(content)
                with self.assertRaisesRegex(ConfigurationError, 'setup-web'):
                    runtime_entry(home)
            metadata.write_text('{"version":"0.2.1-rc.2","bin":{"dsh":"cli.js"}}')
            (package / 'cli.js').write_text('')
            self.assertEqual(runtime_entry(home)[0], (package / 'cli.js').resolve())

    def test_doctor_no_pnpm_needed_for_installed_web_and_no_probe_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            initialize(home)
            def tool(name):
                if name == 'pnpm':
                    raise ConfigurationError('pnpm unavailable')
                return 'node', 'v24.0.0'
            with patch('toollery_dsh.cli.require_tool', side_effect=tool), \
                    patch('toollery_dsh.cli.runtime_entry', return_value=(home / 'cli.js', '0.2.1-rc.2')), \
                    patch('toollery_dsh.cli.importlib.metadata.version', return_value='0.2.1rc2'):
                report = cli.doctor(home / 'toollery.json', require_web=True)
                self.assertTrue(report['ready'], report)
                pnpm = next(item for item in report['checks'] if item['check'] == 'pnpm')
                self.assertFalse(pnpm['required'])
            with patch('toollery_dsh.cli.require_tool', side_effect=ConfigurationError('broken executable')):
                report = cli.doctor(home / 'toollery.json', require_web=True)
                self.assertFalse(report['ready'])

    def test_setup_preflight_happens_before_install_or_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            initialize(home)
            with patch('toollery_dsh.cli.require_tool', side_effect=ConfigurationError('old Node')), \
                    patch('toollery_dsh.cli.subprocess.call') as install:
                with self.assertRaises(ConfigurationError):
                    cli.setup_web(home / 'toollery.json')
                install.assert_not_called()
                self.assertFalse((home / '.toollery/web-runtime').exists())

    def test_cached_runtime_does_not_require_custom_generation_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            initialize(home)
            generation = {'provider': 'command', 'model': 'external-model', 'command': ['./external-generator']}
            (home / 'manual-generator.json').write_text(json.dumps(generation))
            with patch('toollery_dsh.cli.importlib.metadata.version', return_value='0.2.1rc2'), \
                    patch('toollery_dsh.cli.require_tool', side_effect=ConfigurationError('not installed')):
                report = cli.doctor(home / 'toollery.json')
            self.assertTrue(report['ready'], report)
            configured_generator(generation, home, require_executable=False)
            with self.assertRaisesRegex(ConfigurationError, 'Executable'):
                configured_generator(generation, home)

    def test_generator_script_uses_config_directory_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='toollery config ') as folder:
            home = Path(folder)
            script = home / 'local generator.py'
            script.write_text('import json, sys\nr=json.load(sys.stdin)\n'
                              'q=open("relative evidence.txt").read()\n'
                              'entry={"query":q,"scenario_type":"test","generation_notes":"fixture"}\n'
                              'print(json.dumps({"manuals":{r["capabilities"][0]["name"]:[entry]}}))\n')
            (home / 'relative evidence.txt').write_text('read local data')
            config = {'provider': 'command', 'model': 'local-fixture',
                      'command': [sys.executable, 'local generator.py']}
            generator = configured_generator(config, home)
            load_generator_credentials(config, home)  # No API or parameters required.
            evidence = normalize(ToolRecord('t', {'name': 'local', 'description': 'read'}))
            self.assertEqual(generator.generate(evidence, 1).proxy_queries, ['read local data'])
            self.assertNotIn('cwd', generator.contract)  # Runtime cwd is not a new cache identity field.
            load_generator_credentials({'provider': 'mock'}, home)

    def test_source_missing_command_cwd_and_credential_are_actionable(self):
        source = {'id': 'local', 'type': 'mcp', 'config': {'transport': 'stdio', 'command': sys.executable}}
        with tempfile.TemporaryDirectory() as folder, patch('toollery_dsh.sources.runtime_entry'):
            home = Path(folder)
            for config, message in [({**source['config'], 'cwd': 'not-there'}, 'cwd'),
                                    ({**source['config'], 'command': './not-there'}, 'Executable'),
                                    ({**source['config'], 'args': 'invalid'}, 'args')]:
                with self.assertRaisesRegex(ConfigurationError, message):
                    compile_sources([{**source, 'config': config}], home)
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ConfigurationError, 'MISSING_TEST_KEY'):
                    load_source_credentials({'tool_sources': [{**source, 'env_from': {'KEY': 'MISSING_TEST_KEY'}}]}, home)

    def test_port_busy_before_credentials_or_generation(self):
        with tempfile.TemporaryDirectory() as folder, socket.socket() as occupied:
            home = Path(folder)
            initialize(home)
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            args = cli.parser().parse_args(['web', '--config', str(home / 'toollery.json'), '--port', str(occupied.getsockname()[1])])
            with patch('toollery_dsh.web.web_runtime', return_value=(['node', 'entry.js'], 'test')):
                with self.assertRaisesRegex(ConfigurationError, 'Cannot bind Web port'):
                    web_run(args, home)

    @unittest.skipUnless(os.name == 'nt', 'Windows npm shim invocation')
    def test_relative_batch_host_and_setup_shim_preserve_spaces_and_cwd(self):
        with tempfile.TemporaryDirectory(prefix='toollery shim ') as folder:
            home = Path(folder)
            initialize(home)
            script = home / 'record.py'
            record = home / 'arguments.json'
            script.write_text('import json, os, sys\nfrom pathlib import Path\n'
                              'Path("arguments.json").write_text(json.dumps({"cwd":os.getcwd(),"args":sys.argv[1:]}))\n')
            shim = home / 'pnpm.cmd'
            shim.write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n')
            subprocess.run(child_command(['./pnpm.cmd', 'argument with spaces'], cwd=home), cwd=home, check=True)
            proof = json.loads(record.read_text())
            self.assertEqual(Path(proof['cwd']).resolve(), home.resolve())
            self.assertEqual(proof['args'], ['argument with spaces'])
            with patch.dict(os.environ, {'PATH': str(home)}), patch('toollery_dsh.cli.require_tool'):
                self.assertEqual(cli.setup_web(home / 'toollery.json'), 0)
            proof = json.loads(record.read_text())
            self.assertEqual(Path(proof['cwd']).resolve(), home.resolve())
            # load_config canonicalizes Windows 8.3 paths (for example RUNNER~1)
            # before setup builds its arguments. Compare the canonical target.
            self.assertEqual(proof['args'], ['--dir', str((home / '.toollery/web-runtime').resolve()), 'install', '--frozen-lockfile', '--ignore-scripts', '--reporter=append-only'])
