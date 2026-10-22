"""Cross-startup MCP inventory/cache/retrieval/execution acceptance test.

Uses a real DSH process and a real controlled stdio MCP server per stage.
Only the LLM and manual generator are scripted unless --real is supplied.
"""
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
from dsh_demo import compact_result, load_key, write_patch
from dsh_mock import MockMessages
from dsh_sources import source_environment
from toollery.adapters.dsh import DSHBridge
from toollery.evidence import normalize
from toollery_dsh.configuration import resolve_generator
from toollery.generation import MockGenerator, make_generator
from toollery.manuals import ManualCache, cache_identity
from toollery.storage import atomic_json, file_digest

PREFIX = 'mcp__lifecycle__'


def tool(name, description, parameter='text'):
    return {'name': name, 'description': description, 'inputSchema': {
        'type': 'object', 'properties': {parameter: {'type': 'string'}},
        'required': [parameter], 'additionalProperties': False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true', help='Use real model and manual API (paid requests)')
    args = parser.parse_args()
    config = json.loads((ROOT / 'examples/dsh/filesystem.json').read_text())
    output = ROOT / ('runs/dsh-lifecycle-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True)
    cache = ManualCache(output / 'manual-cache')
    manifest, server_log = output / 'inventory.json', output / 'mcp-server.jsonl'
    # The source identity, command and manifest path remain identical at every boot.
    sources = [{'id': 'lifecycle', 'type': 'mcp', 'config': {
        'transport': 'stdio', 'command': sys.executable,
        'args': [str(ROOT / 'tests/fixtures/mcp_lifecycle_server.py'), str(manifest), str(server_log)]}}]
    generation = json.loads((ROOT / 'examples/generator.command.json').read_text())
    if args.real:
        key = load_key(config['api_key_env'])
        load_key(generation['parameters']['api_key_env'])
        generator = make_generator(resolve_generator(generation))
    else:
        key, generator = 'local-mock-key', MockGenerator()
    base = [tool('amber_measure', 'Measure amber samples and echo the supplied text.'),
            tool('quartz_encode', 'Encode quartz samples and echo the supplied text.'),
            tool('legacy_echo', 'Echo legacy sample text.')]
    added = tool('cobalt_stamp', 'Stamp cobalt samples and echo the supplied text.')
    revised = tool('cobalt_stamp', 'Stamp certified cobalt samples with the updated certified workflow and echo text.')
    revised_param = tool('cobalt_stamp', revised['description'], 'message')
    stages = [
        ('A_initial', base, None, 'amber_measure', 'text'),
        ('B_added', base + [added], {'added': [PREFIX + 'cobalt_stamp'], 'changed': [], 'removed': []}, 'cobalt_stamp', 'text'),
        ('C_restart', base + [added], {'added': [], 'changed': [], 'removed': []}, 'cobalt_stamp', 'text'),
        ('D_description_removed', base[:2] + [revised], {'added': [], 'changed': [PREFIX + 'cobalt_stamp'], 'removed': [PREFIX + 'legacy_echo']}, 'cobalt_stamp', 'text'),
        ('E_parameter', base[:2] + [revised_param], {'added': [], 'changed': [PREFIX + 'cobalt_stamp'], 'removed': []}, 'cobalt_stamp', 'message'),
        ('F_restart', base[:2] + [revised_param], {'added': [], 'changed': [], 'removed': []}, 'cobalt_stamp', 'message'),
    ]
    previous, previous_manuals, reports = {}, {}, []
    active_parameter = 'text'

    def choose(prompt, _tools):
        name = 'amber_measure' if 'amber_measure' in prompt else 'cobalt_stamp'
        return PREFIX + name, {active_parameter: 'lifecycle-ok'}

    with ExitStack() as stack:
        model = None if args.real else stack.enter_context(MockMessages(choose))
        for label, inventory, expected_delta, target, active_parameter in stages:
            directory = output / label
            directory.mkdir()
            atomic_json(manifest, {'stage': label, 'tools': inventory})
            before_files = {p.name: file_digest(p) for p in cache.root.glob('manuals/*.json')}
            preparation = {}

            def prepare(bridge, session_id):
                preparation.update(bridge.prepare(session_id, generator, cache, workers=8))

            # Fresh bridge, searches, SDK client, DSH runtime and MCP process.
            # The only manual state carried across boots is the cache on disk.
            with DSHBridge(directory, k=3, on_snapshot=prepare) as bridge:
                patch = directory / 'cordis.patch.json'
                write_patch(patch, config['model'], sources)
                request_start = len(model.requests) if model else 0
                with DeepSeekHarness(DeepSeekHarnessConfig(
                    provider='deepseek-official', model=config['model'], max_tokens=4096,
                    cwd=str(ROOT), runtime_cwd=str(ROOT), profile='sdk-minimal', patches=(str(patch),),
                    dsh_home=str(directory / 'home'),
                    env={'TOOLLERY_BRIDGE_URL': bridge.url, 'TOOLLERY_BRIDGE_TOKEN': bridge.token,
                         **source_environment(sources)},
                    base_url=config['base_url'] if args.real else model.url, api_key=key,
                    initialize_timeout_seconds=60, request_timeout_seconds=180, shutdown_timeout_seconds=10,
                )) as harness:
                    session = harness.start_session()
                    session.get_working_directory()
                    state = bridge.sessions[session.id]
                    current = {name: item.spec_hash for name, item in state.pool.tools.items()}
                    assert {name for name in current if name.startswith(PREFIX)} == {PREFIX + t['name'] for t in inventory}
                    delta = {'added': sorted(current.keys() - previous.keys()),
                             'removed': sorted(previous.keys() - current.keys()),
                             'changed': sorted(name for name in current.keys() & previous.keys() if current[name] != previous[name])}
                    if expected_delta is not None:
                        assert delta == expected_delta, (label, delta, expected_delta)
                    new_count = len(delta['added']) + len(delta['changed'])
                    assert preparation['generated'] == preparation['generation_calls'] == new_count, preparation
                    assert preparation['cache_hits'] == len(current) - new_count, preparation
                    assert preparation['valid'] == len(current) and preparation['ready']
                    manuals = {}
                    for name, item in state.pool.tools.items():
                        record = cache.read(cache_identity(item, normalize(item), generator, 3))
                        assert record is not None
                        manuals[name] = record['manual_key']
                        if name in previous_manuals:
                            assert (manuals[name] != previous_manuals[name]) == (name in delta['changed'])
                    # All three newly constructed indexes must exactly match this boot's pool.
                    for search in state.searches.values():
                        assert set(search.index.tool_ids) == set(current)
                        all_results = search.select('legacy_echo cobalt_stamp', budget='all')
                        assert {item.tool_id for item in all_results} == set(current)
                    expected_name = PREFIX + target
                    prompt = (f'Use {expected_name} with {active_parameter} set to "lifecycle-ok". '
                              'Call the tool exactly once and return its exact result.')
                    result = session.run(prompt)
                    atomic_json(directory / 'turn.json', compact_result(result))
                    assert result.finish_reason == 'completed', result.finish_reason
                    selection = bridge.selections[-1]
                    assert selection['enabled'] and selection['budget'] == 'top_k'
                    assert expected_name in selection['selected_names'], selection
                    calls = [c for c in bridge.calls if c['selection_id'] == selection['selection_id']]
                    assert len(calls) == 1 and calls[0]['name'] == expected_name and calls[0]['status'] == 'success', calls
                    expected_text = target + ':lifecycle-ok'
                    events = [e for e in result.events if e.get('type') == 'tool/result']
                    assert events and expected_text in json.dumps(events), events
                    assert not any(e['data']['message'].get('isError') for e in events)
                    usage = {}
                    for event in result.events:
                        if event.get('type') == 'assistant/message':
                            for name, value in event['data'].get('usage', {}).items():
                                if isinstance(value, (int, float)) and not isinstance(value, bool):
                                    usage[name] = usage.get(name, 0) + value
                    if model:
                        requests = model.requests[request_start:]
                        assert len(requests) == 2
                        for request in requests:
                            assert {t['name'] for t in request.get('tools', [])} == set(selection['selected_names'])
                            for schema in request.get('tools', []):
                                original = state.pool.tools[schema['name']].documentation
                                assert schema['description'] == original['description']
                                assert schema['input_schema'] == original['parameters']
                    report = {'stage': label, 'passed': True, 'pool_count': len(current),
                              'snapshot_hash': state.pool.snapshot_hash, 'delta': delta,
                              'preparation': dict(preparation), 'manual_keys': manuals,
                              'selected_names': selection['selected_names'], 'calls': calls,
                              'model_usage_reported': usage,
                              'index_membership_all_modes': True,
                              'original_schemas_checked_on_wire': bool(model)}
                # Verify actual server discovery and execution, independently of model text.
                protocol = [json.loads(line) for line in server_log.read_text().splitlines()]
                stage_protocol = [row for row in protocol if row['stage'] == label]
                assert any(row['method'] == 'tools/list' for row in stage_protocol)
                executed = [row for row in stage_protocol if row['method'] == 'executed']
                assert len(executed) == 1 and executed[0]['result'] == expected_text, executed
                assert executed[0]['arguments'] == {active_parameter: 'lifecycle-ok'}
                report['server_pid'] = executed[0]['pid']
                report['server_discovery_and_execution_verified'] = True
            after_files = {p.name: file_digest(p) for p in cache.root.glob('manuals/*.json')}
            assert all(after_files.get(name) == value for name, value in before_files.items()), 'Prior cache files were modified'
            assert len(after_files) - len(before_files) == new_count
            expected_new_files = {cache.path(manuals[name]).name for name in delta['added'] + delta['changed']}
            assert after_files.keys() - before_files.keys() == expected_new_files
            report['generated_tool_ids'] = sorted(delta['added'] + delta['changed'])
            report['prior_cache_bytes_preserved'] = True
            reports.append(report)
            atomic_json(directory / 'validation.json', report)
            previous, previous_manuals = current, manuals
            print(f"{label}: pool={len(current)} hits={preparation['cache_hits']} generated={new_count}; selected+executed={expected_name}", flush=True)
        generation_usage, conversation_usage = {}, {}
        for report in reports:
            for key, value in report['preparation']['usage_from_new_generations'].items():
                generation_usage[key] = generation_usage.get(key, 0) + value
            for key, value in report['model_usage_reported'].items():
                conversation_usage[key] = conversation_usage.get(key, 0) + value
        atomic_json(output / 'validation.json', {'passed': True, 'real_model': args.real,
            'same_source_configuration': sources, 'fresh_bridge_and_runtime_per_stage': True,
            'shared_disk_cache_only': True, 'stages': reports,
            'manual_generation_usage': generation_usage, 'conversation_usage': conversation_usage,
            'scope': 'Explicit fixture prompts verify lifecycle plumbing, not retrieval quality.'})
        if model:
            atomic_json(output / 'mock-requests.json', model.requests)
    print('Passed. Reports: ' + str(output), flush=True)


if __name__ == '__main__':
    main()
