"""Launcher boundaries: local credentials, authenticated HTTP and process lifecycle."""
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from toollery_dsh.configuration import ConfigurationError, initialize
from toollery_dsh.launcher import Launcher, available_port, make_server, read_keys, validate_settings


class LauncherTests(unittest.TestCase):
    def test_install_progress_exposes_only_counters_before_web_start(self):
        with tempfile.TemporaryDirectory() as folder:
            launcher = Launcher(folder)
            states = []

            def spawn(command, env, cwd):
                states.append(launcher.snapshot())
                output = ('untrusted output with sentinel-secret\n'
                          'Progress: resolved 120, reused 40, downloaded 60, added 100\n'
                          'Progress: resolved 120, reused 40, downloaded 80, added 120, done\n'
                          if 'setup-web' in command else '')
                child = SimpleNamespace(stdout=io.StringIO(output), wait=lambda: 0, poll=lambda: 0)
                launcher.process = child
                return child

            with patch('toollery_dsh.launcher.runtime_entry', side_effect=ConfigurationError('missing')):
                with patch.object(launcher, '_spawn', side_effect=spawn):
                    launcher._run(validate_settings(dict(offline=True)), {})
            self.assertEqual([state['stage'] for state in states], ['installing', 'starting'])
            self.assertEqual(states[1]['packages'], dict(resolved=120, reused=40, downloaded=80, added=120))
            self.assertNotIn('sentinel-secret', json.dumps(states))

    def test_port_conflict_and_invalid_configuration(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            port = occupied.getsockname()[1]
            self.assertNotEqual(available_port(port), port)
        for body in ({'port': True}, {'k': 0}, {'pool': 'filesystem', 'offline': True},
                     {'chat_url': 'https://user:secret@example.org'}, {'chat_key': 'a\nb'},
                     {'manual_url': None}, {'enabled': 'false'}, {'pool': []}, {'mode': {}}):
            with self.subTest(body=body), self.assertRaises(ConfigurationError):
                validate_settings(body)

    def test_keys_stay_local_and_start_options_reach_child(self):
        system_root = os.environ.get('SystemRoot')
        with tempfile.TemporaryDirectory(prefix='toollery launcher ') as folder, patch.dict(os.environ, {}, clear=True):
            # Preserve the OS executable lookup needed by taskkill on Windows.
            if os.name == 'nt':
                os.environ['SystemRoot'] = system_root
            launcher = Launcher(folder)
            calls = []
            original = launcher._spawn

            def spawn(command, env, cwd):
                calls.append((command, env, cwd))
                port = command[command.index('--port') + 1]
                return original([sys.executable, '-u', '-c',
                    f'import time; print("dsh web: http://127.0.0.1:{port}/?token=test-token", flush=True); time.sleep(120)'], env, cwd)

            body = dict(offline=True, enabled=False, k=5, mode='manual',
                        chat_key='sentinel-chat', manual_key='sentinel-manual')
            with patch('toollery_dsh.launcher.runtime_entry'), patch.object(launcher, '_spawn', side_effect=spawn):
                try:
                    launcher.start(body)
                    deadline = time.monotonic() + 10
                    while launcher.snapshot()['phase'] == 'starting' and time.monotonic() < deadline:
                        time.sleep(.02)
                    status = launcher.snapshot()
                    self.assertEqual(status['phase'], 'ready', status)
                    serialized = json.dumps(status)
                    self.assertNotIn('sentinel-chat', serialized)
                    self.assertNotIn('sentinel-manual', serialized)
                    command, env, home = calls[0]
                    self.assertIn('--offline', command)
                    self.assertIn('--toollery-off', command)
                    self.assertEqual(command[command.index('--k') + 1], '5')
                    self.assertEqual(env['TOOLLERY_CHAT_API_KEY'], 'sentinel-chat')
                    self.assertEqual(env['TOOLLERY_MANUAL_API_KEY'], 'sentinel-manual')
                    with self.assertRaises(ConfigurationError):
                        launcher.start(body)
                    config = json.loads(Path(command[command.index('--config') + 1]).read_text())
                    self.assertIn('/launcher/', config['web_home'])
                    self.assertTrue(Path(config['_launcher_workspace']).is_dir())
                finally:
                    launcher.stop()
            self.assertIsNotNone(launcher.process.poll())
            self.assertFalse(launcher.worker.is_alive())
            self.assertEqual(read_keys(folder)['TOOLLERY_MANUAL_API_KEY'], 'sentinel-manual')
            saved = (Path(folder) / 'settings.json').read_text()
            self.assertNotIn('sentinel-', saved)
            self.assertNotIn('TOOLLERY_CHAT_API_KEY', os.environ)
            self.assertFalse(list((Path(folder) / 'demo').glob('.toollery-launch-*.json')))

    def test_custom_configuration_preserves_user_models_and_generator(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            initialize(home / 'custom')
            path = home / 'custom/toollery.json'
            before = path.read_bytes()
            generator = path.with_name('manual-generator.json')
            before_generator = generator.read_bytes()
            launcher = Launcher(home / 'launcher')
            with patch.object(launcher, '_run') as run:
                launcher.start(dict(pool='custom', custom_config=str(path)))
                launcher.worker.join(2)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(generator.read_bytes(), before_generator)
            self.assertTrue(run.called)

    def test_http_rejects_missing_token_cross_origin_and_rebinding(self):
        with tempfile.TemporaryDirectory() as folder:
            launcher = Launcher(folder)
            server, token = make_server(launcher)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f'http://127.0.0.1:{server.server_port}'

            def request(method, path, headers=None, body=None):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                data = response.read()
                connection.close()
                return response.status, data

            try:
                self.assertEqual(request('GET', '/')[0], 200)
                self.assertEqual(request('GET', '/waiting')[0], 200)
                self.assertEqual(request('GET', '/waiting.js')[0], 200)
                self.assertEqual(request('GET', '/api/status')[0], 403)
                headers = {'X-Toollery-Token': token}
                self.assertEqual(request('GET', '/api/status', headers)[0], 200)
                self.assertEqual(request('GET', '/api/status', {**headers, 'Host': 'evil.example'})[0], 403)
                self.assertEqual(request('POST', '/api/start', {**headers, 'Origin': 'https://evil.example'}, '{}')[0], 403)
                self.assertEqual(request('POST', '/api/start', {**headers, 'Origin': origin}, '{broken')[0], 400)
                self.assertEqual(request('POST', '/api/stop', {**headers, 'Origin': origin}, '{}')[0], 200)
                self.assertEqual(launcher.snapshot()['phase'], 'stopped')
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)


if __name__ == '__main__':
    unittest.main()
