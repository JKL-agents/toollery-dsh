"""Release boundaries: installation resources, config ownership and credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from toollery_dsh.cli import parser
from toollery_dsh.bridge import child_command
from toollery_dsh.configuration import (ASSETS, initialize, load_config,
                                        generator_config, load_key)


class PackagingTests(unittest.TestCase):
    def test_init_preserves_edits_and_resolves_from_config_directory(self):
        with tempfile.TemporaryDirectory(prefix='toollery packaging ') as folder:
            home = Path(folder) / 'user workspace'
            initialize(home, 'filesystem')
            path = home / 'toollery.json'
            config = json.loads(path.read_text())
            config['k'] = 7
            path.write_text(json.dumps(config))
            result = initialize(home, 'filesystem')
            self.assertFalse(result['created'])
            parsed, resolved = load_config(path)
            self.assertEqual(parsed['k'], 7)
            self.assertEqual(resolved, home.resolve())
            self.assertTrue((resolved / parsed['tool_sources'][0]['root'] / 'welcome.txt').is_file())
            generation = generator_config(parsed, resolved)
            self.assertNotEqual(generation['command'][0], '{python}')
            self.assertTrue(Path(generation['command'][0]).is_file())

    def test_independent_credentials_and_shared_fallback(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            home = Path(folder)
            (home / '.env').write_text('TOOLLERY_API_KEY=shared-test\nTOOLLERY_CHAT_API_KEY=chat-test\nTOOLLERY_MANUAL_API_KEY=manual-test\nUNRELATED=ignored\n')
            self.assertEqual(load_key('TOOLLERY_CHAT_API_KEY', home), 'chat-test')
            self.assertEqual(load_key('TOOLLERY_MANUAL_API_KEY', home), 'manual-test')
            self.assertNotIn('UNRELATED', os.environ)
            os.environ.pop('TOOLLERY_MANUAL_API_KEY')
            (home / '.env').write_text('TOOLLERY_API_KEY=shared-test\n')
            self.assertEqual(load_key('TOOLLERY_MANUAL_API_KEY', home), 'shared-test')

    def test_installed_resources_are_complete(self):
        for name in ('demo-tools.mjs', 'dsh/toollery.mjs', 'dsh/sources.mjs',
                     'dsh/web-controls.mjs', 'dsh/ui/index.js', 'dsh/ui/client.js',
                     'dsh/ui/package.json', 'dsh/web-runtime/package.json',
                     'dsh/web-runtime/pnpm-lock.yaml', 'playground/welcome.txt'):
            with self.subTest(name=name):
                self.assertTrue((ASSETS / name).is_file())

    def test_existing_host_command_keeps_its_own_arguments(self):
        args = parser().parse_args(['bridge', '--k', '5', '--', 'dsh', '--profile', 'web'])
        self.assertEqual(args.launch, ['--', 'dsh', '--profile', 'web'])
        self.assertEqual(args.k, 5)

    @unittest.skipUnless(os.name == 'nt', 'Windows batch shim boundary')
    def test_windows_batch_preserves_workspace_and_arguments(self):
        with tempfile.TemporaryDirectory(prefix='toollery batch ') as folder:
            home = Path(folder)
            script = home / 'fake host.py'
            script.write_text('import json, os, sys\nprint(json.dumps({"cwd": os.getcwd(), "args": sys.argv[1:]}))\n')
            shim = home / 'fake host.cmd'
            shim.write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n')
            result = subprocess.check_output(
                child_command([str(shim), '--sentinel', 'argument with spaces']),
                cwd=home, text=True)
            proof = json.loads(result)
            self.assertEqual(Path(proof['cwd']).resolve(), home.resolve())
            self.assertEqual(proof['args'], ['--sentinel', 'argument with spaces'])


if __name__ == '__main__':
    unittest.main()
