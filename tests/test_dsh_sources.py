import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from dsh_demo import write_patch
from dsh_sources import compile_sources
from dsh_web import write_web_patch


class SourceConfigTests(unittest.TestCase):
    def setUp(self):
        runtime = patch('toollery_dsh.sources.runtime_entry')
        runtime.start()
        self.addCleanup(runtime.stop)
    def test_demo_stays_default_and_configured_pool_excludes_demo(self):
        with tempfile.TemporaryDirectory() as folder:
            patch = Path(folder) / 'patch.json'
            write_patch(patch, 'test')
            self.assertIn('toollery-demo-tools', patch.read_text())
            write_patch(patch, 'test', [{'id': 'fs', 'type': 'mcp', 'config': {
                'transport': 'stdio', 'command': sys.executable, 'args': ['server.mjs']}}])
            rows = json.loads(patch.read_text())
            self.assertNotIn('toollery-demo-tools', patch.read_text())
            source = rows[-1]['insert'][0]['config']['toolSources'][0]
            self.assertTrue(source['config']['failOnStartupError'])
            self.assertEqual(source['config']['cwd'], str(ROOT))

    def test_invalid_or_duplicate_sources_and_inline_credentials_rejected(self):
        item = {'id': 'x', 'type': 'mcp', 'config': {'transport': 'stdio', 'command': sys.executable}}
        for sources in ([], [item, item], [{'id': 'bad id', 'type': 'mcp'}],
                        [{**item, 'config': {**item['config'], 'env': {'API_KEY': 'value'}}}]):
            with self.assertRaises(ValueError):
                compile_sources(sources)

    def test_web_source_preset_and_persona(self):
        config = json.loads((ROOT / 'examples/dsh/filesystem.json').read_text())
        config['tool_sources'] = [{'id': 'test', 'type': 'mcp', 'config': {'transport': 'stdio', 'command': sys.executable}}]
        with tempfile.TemporaryDirectory() as folder:
            patch = Path(folder) / 'patch.json'
            write_web_patch(patch, config, 'http://127.0.0.1:1')
            text = patch.read_text()
            self.assertIn('toollery-filesystem', text)
            self.assertNotIn('toollery-demo-tools', text)
            self.assertIn('real filesystem tools', text)


if __name__ == '__main__':
    unittest.main()
