"""Real MCP discovery/execution/cache tests; local scripted model by default."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
from dsh_demo import load_key, write_patch, compact_result
from dsh_mock import MockMessages
from dsh_sources import source_environment
from setup_dsh_playground import setup
from toollery.adapters.dsh import DSHBridge
from toollery_dsh.configuration import resolve_generator
from toollery.generation import MockGenerator, make_generator
from toollery.manuals import ManualCache
from toollery.storage import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true', help='Use configured real model/manual generator (paid requests)')
    args = parser.parse_args()
    config = json.loads((ROOT / 'examples/dsh/filesystem.json').read_text())
    playground = setup()
    config['tool_sources'][0]['root'] = str(playground)
    output = ROOT / ('runs/dsh-filesystem-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True)
    cache = ManualCache(ROOT / config['cache_dir'] if args.real else output / 'mock-manual-cache')
    generation = json.loads((ROOT / 'examples/generator.command.json').read_text())
    if args.real:
        key = load_key(config['api_key_env'])
        load_key(generation['parameters']['api_key_env'])
        generator = make_generator(resolve_generator(generation))
    else:
        key, generator = 'local-mock-key', MockGenerator()

    def choose(prompt, tools):
        # Explicit names make this a plumbing test, not retrieval-quality evidence.
        if 'mcp__filesystem__write_file' in prompt:
            return 'mcp__filesystem__write_file', {'path': str(playground / 'smoke-note.txt'), 'content': 'Toollery MCP integration works.\n'}
        if 'mcp__filesystem__read_text_file' in prompt:
            filename = 'smoke-note.txt' if 'smoke-note.txt' in prompt else 'welcome.txt'
            return 'mcp__filesystem__read_text_file', {'path': str(playground / filename)}
        return None

    preparations, turns, discoveries = [], [], []
    def prepare(bridge, session_id):
        report = bridge.prepare(session_id, generator, cache, workers=8)
        preparations.append(report)
        print(f"Ready: {report['valid']}/{report['total']}; generated={report['generated']}; cache_hits={report['cache_hits']}", flush=True)

    with ExitStack() as stack:
        model = None if args.real else stack.enter_context(MockMessages(choose))
        bridge = stack.enter_context(DSHBridge(output, k=3, on_snapshot=prepare))

        def launch(sources, label):
            patch = output / (label + '.patch.json')
            write_patch(patch, config['model'], sources)
            return DeepSeekHarness(DeepSeekHarnessConfig(
                provider='deepseek-official', model=config['model'], max_tokens=4096,
                cwd=str(ROOT), runtime_cwd=str(ROOT), profile='sdk-minimal', patches=(str(patch),),
                dsh_home=str(output / (label + '-home')),
                env={'TOOLLERY_BRIDGE_URL': bridge.url, 'TOOLLERY_BRIDGE_TOKEN': bridge.token,
                     **source_environment(sources)}, base_url=config['base_url'] if args.real else model.url,
                api_key=key, initialize_timeout_seconds=60, request_timeout_seconds=180,
                shutdown_timeout_seconds=10,
            ))

        with launch(config['tool_sources'], 'initial') as harness:
            session = harness.start_session()
            session.get_working_directory()
            state = bridge.sessions[session.id]
            names = set(state.pool.tool_ids)
            assert 'mcp__filesystem__read_text_file' in names, names
            assert 'mcp__filesystem__write_file' in names, names
            assert 'add_numbers' not in names
            discoveries.append({'stage': 'initial', 'tool_count': len(names)})
            cases = [
                (True, f'Use mcp__filesystem__read_text_file to read {playground / "welcome.txt"}.', 'Welcome'),
                (False, f'Use mcp__filesystem__write_file to write exactly "Toollery MCP integration works.\\n" into {playground / "smoke-note.txt"}.', None),
                (True, f'Use mcp__filesystem__read_text_file to read {playground / "smoke-note.txt"}.', 'Toollery MCP integration works.'),
            ]
            for enabled, prompt, expected in cases:
                bridge.configure(session.id, enabled=enabled)
                result = session.run(prompt)
                turns.append(compact_result(result))
                atomic_json(output / 'turns.json', turns)
                assert result.finish_reason == 'completed', result.finish_reason
                selection = bridge.selections[-1]
                calls = [c for c in bridge.calls if c['selection_id'] == selection['selection_id']]
                assert calls and all(c['status'] == 'success' for c in calls), calls
                assert len(selection['selected_names']) == (3 if enabled else len(names))
                if expected:
                    assert expected in json.dumps([event for event in result.events if event.get('type') == 'tool/result'])
                print(f"{'ON' if enabled else 'OFF'}: {len(selection['selected_names'])}/{len(names)} selected; calls={[c['name'] for c in calls]}", flush=True)
            assert (playground / 'smoke-note.txt').read_text().strip() == 'Toollery MCP integration works.'
            if model:
                for request in model.requests:
                    for tool in request.get('tools', []):
                        original = state.pool.tools[tool['name']].documentation
                        assert tool['description'] == original['description']
                        assert tool['input_schema'] == original['parameters']

        # A fresh runtime must discover again but reuse all unchanged manuals.
        with launch(config['tool_sources'], 'restart') as harness:
            harness.start_session().get_working_directory()
            assert preparations[-1]['generated'] == 0
            assert preparations[-1]['cache_hits'] == len(names)

        # Small native plugin demonstrates a second source and per-tool delta reuse.
        sources = config['tool_sources'] + [{'id': 'extra', 'type': 'plugin', 'path': 'tests/fixtures/dsh-extra-tool.mjs'}]
        with launch(sources, 'expanded') as harness:
            session = harness.start_session()
            session.get_working_directory()
            expanded = bridge.sessions[session.id]
            assert set(expanded.pool.tool_ids) == names | {'fixture_echo'}
            assert preparations[-1]['cache_hits'] == len(names)
            assert preparations[-1]['generated'] in (0, 1) if args.real else preparations[-1]['generated'] == 1
            discoveries.append({'stage': 'expanded', 'tool_count': len(expanded.pool.tools)})

        # A successful first source plus a broken second source is NOT a pool.
        bad = config['tool_sources'] + [{'id': 'broken', 'type': 'mcp', 'config': {
            'transport': 'stdio', 'command': str(ROOT / 'work/no-such-mcp-executable.exe')}}]
        before = len(bridge.sessions)
        rejected = False
        with launch(bad, 'failure') as harness:
            try:
                harness.start_session().get_working_directory()
            except Exception:
                rejected = True
        assert rejected and len(bridge.sessions) == before, 'Partial snapshot was published'
        if model:
            atomic_json(output / 'mock-requests.json', model.requests)
        atomic_json(output / 'validation.json', {
            'passed': True, 'offline_mock': not args.real, 'real_mcp_execution': True,
            'turns': len(turns), 'discoveries': discoveries, 'preparations': preparations,
            'restart_cache_reused': True, 'new_source_generates_only_new_manual': True,
            'source_failure_blocks_partial_snapshot': True,
            'original_schemas_preserved_on_mock_wire': bool(model),
            'selection_breakdown': bridge.selections,
        })
    print('Passed. Reports: ' + str(output), flush=True)


if __name__ == '__main__':
    main()
