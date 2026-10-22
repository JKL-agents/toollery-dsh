"""Persistent DSH session + startup Toollery preparation + per-prompt selection."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

from .configuration import (ASSETS, load_config, generator_config as read_generator, load_key,
                            configured_generator, load_generator_credentials, load_source_credentials)

from toollery.adapters.dsh import DSHBridge
from toollery.generation import MockGenerator
from toollery.manuals import ManualCache
from toollery.storage import atomic_json
from .sources import compile_sources, source_environment


def write_patch(path, model, tool_sources=None, home=None):
    home = Path(home) if home is not None else Path.cwd()
    rows = [{'id': name, 'disabled': True} for name in (
        'tool-fs', 'tool-working-directory', 'persistent-bash', 'persistent-pwsh',
        'terminal-bash', 'terminal-pwsh', 'pty',
    )]
    rows.extend([
        {'id': 'tools', 'config': {'mode': 'native'}},
        # The released deepseek-flash catalog enables addition-only deferred
        # updates. Disjoint per-turn subsets can then defer EVERY tool, which
        # the live API rejects (400). Use the provider's supported ordinary
        # complete-current-list projection, retaining the same session/history.
        {'id': 'llm-deepseek', 'config': {'models': [
            {'id': model, 'contextWindow': 1000000, 'inputModalities': ['text']},
        ]}},
        {'id': 'system-prompt', 'config': {'personaPrefix': (
            'You are a helpful assistant using local utility tools. When a user requests a calculation or transformation, '
            'use an available matching tool. If none matches, say so. Do not invent tool names. '
            'Tool availability can change between user messages. Respond in the user\'s language.'
        )}},
        {'insert': [
            *([{'id': 'toollery-demo-tools', 'name': (ASSETS / 'demo-tools.mjs').as_uri()}]
              if tool_sources is None else []),
            {'id': 'toollery', 'name': (ASSETS / 'dsh/toollery.mjs').as_uri(),
             'config': {'toolSources': compile_sources(tool_sources, home) if tool_sources is not None else []}},
        ]},
    ])
    # JSON is valid YAML. Keep the file local and free of credentials.
    atomic_json(path, rows)


def compact_result(result):
    return {
        'session_id': result.session_id, 'final_response': result.final_response,
        'finish_reason': result.finish_reason, 'events': result.events,
    }


def report_summary(output, bridge, results, *, offline):
    usage = {}
    for result in results:
        for event in result['events']:
            # Stream chunks repeat usage; count only the final per-step record.
            if event.get('type') == 'assistant/message':
                for key, value in event['data'].get('usage', {}).items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        usage[key] = usage.get(key, 0) + value
    summary = {
        'offline_mock': offline,
        'turns': len(results),
        'completed_turns': sum(r['finish_reason'] == 'completed' for r in results),
        'session_ids': sorted({r['session_id'] for r in results}),
        'same_session': len({r['session_id'] for r in results}) == 1,
        'tool_calls': sum(e.get('type') == 'tool/call' for r in results for e in r['events']),
        'tool_errors': sum(bool(e['data']['message'].get('isError')) for r in results for e in r['events'] if e.get('type') == 'tool/result'),
        'model_usage_reported': usage,
        'manual_preparation': next(iter(bridge.sessions.values())).preparation,
        'selection_breakdown': [{key: s[key] for key in ('turn', 'representation', 'budget', 'k', 'full_pool_count', 'selected_names', 'retrieval_ms')} for s in bridge.selections],
        'cost': 'Token usage only; no price assumption or full-billing claim.',
    }
    atomic_json(output / 'summary.json', summary)
    return summary


def validate_offline(bridge, results, mock_requests):
    selections = bridge.selections
    assert len(results) == len(selections) == 6, 'One retrieval per user turn required'
    assert len({r['session_id'] for r in results}) == 1, 'Session was restarted'
    assert all(r['finish_reason'] == 'completed' for r in results)
    expected = ['add_numbers', 'celsius_to_fahrenheit', 'multiply_numbers']
    for selected, name in zip(selections, expected):
        assert selected['selected_names'] == [name], (name, selected['selected_names'])
    assert len(selections[3]['selected_names']) == selections[3]['full_pool_count'] == 12
    assert selections[4]['selected_names'] == [], 'No-hit must remain empty'
    assert selections[5]['selected_names'] == ['add_numbers']
    assert '42' in results[0]['final_response'] and '68' in results[1]['final_response']
    denied = [e for e in results[5]['events'] if e.get('type') == 'tool/result']
    assert denied and denied[0]['data']['message']['isError'], 'Excluded tool was not denied'
    assert 'outside the current prompt selection' in json.dumps(denied), 'Expected Toollery guard denial'
    for observation in bridge.observations:
        selected = next(s for s in selections if s['selection_id'] == observation['selection_id'])
        assert set(observation['model_tool_names']) == set(selected['selected_names'])
    assert len(mock_requests) == 11, 'Expected five tool rounds and one no-hit round'
    assert len(mock_requests[-1]['messages']) > len(mock_requests[1]['messages']), 'History was lost'
    # All schemas received by the model must be exact projections of original specs.
    state = next(iter(bridge.sessions.values()))
    for request in mock_requests:
        assert not any(tool.get('defer_loading') for tool in request.get('tools', [])), 'Unsafe deferred subset projection'
        for tool in request.get('tools', []):
            original = state.pool.tools[tool['name']].documentation
            assert tool['description'] == original['description']
            assert tool['input_schema'] == original['parameters']
    # Inspect actual wire inputs after the disjoint subset switch. Execution
    # history must survive, with scope notices attached to the right user turn.
    for request, selection in zip((mock_requests[i] for i in (0, 2, 4, 6, 8, 9)), selections):
        system = request['system']
        assert 'absence from the current tool list is not evidence that a past call was invented or failed' in system
        notices = [block['text'] for message in request['messages'] for block in message['content']
                   if block['type'] == 'text' and 'Tool scope: user turn ' in block['text']]
        assert len(notices) == selection['turn'], 'Scope notice must be admitted once per user turn'
        assert f"Tool scope: user turn {selection['turn']}; {len(selection['selected_names'])} tools currently callable." in notices[-1]
    switched = mock_requests[2]
    assert 'add_numbers' not in [tool['name'] for tool in switched.get('tools', [])]
    previous_calls = [block for message in switched['messages'] for block in message['content']
                      if block['type'] == 'tool_use' and block['name'] == 'add_numbers']
    assert len(previous_calls) == 1, 'Previous native tool call disappeared'
    previous_results = [block for message in switched['messages'] for block in message['content']
                        if block['type'] == 'tool_result' and block['tool_use_id'] == previous_calls[0]['id']]
    assert len(previous_results) == 1 and not previous_results[0].get('is_error')
    assert json.loads(previous_results[0]['content'][0]['text']) == {'sum': 42}
    return {'passed': True, 'turns': 6, 'model_requests': len(mock_requests), 'same_session': True,
            'real_tool_execution': True, 'per_prompt_refresh': True, 'guard_denial': True,
            'empty_selection': True, 'all_and_three_representations': True,
            'original_specs_preserved': True, 'history_retained': True,
            'per_turn_scope_context': True, 'historical_call_and_result_after_scope_change': True,
            'model_and_manuals': 'scripted_mock_not_quality_evidence'}


def run(args, output):
    from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
    config, ROOT = load_config(args.config)
    load_source_credentials(config, ROOT)
    if args.k is not None:
        config['k'] = args.k
    if args.mode:
        config['representation'] = args.mode
    if args.budget:
        config['budget'] = args.budget
    write_patch(output / 'cordis.patch.json', config['model'], config.get('tool_sources'), ROOT)
    generator_config = read_generator(config, ROOT)
    generator = MockGenerator() if args.offline_smoke else configured_generator(
        generator_config, ROOT, require_executable=not args.cache_only and not args.spec_only and not args.toollery_off)
    key = 'local-mock-key' if args.offline_smoke else (load_key(config['api_key_env'], ROOT, required=not args.prepare_only) or 'preparation-only-no-chat')
    if not args.offline_smoke and not args.spec_only and not args.cache_only and not args.toollery_off:
        load_generator_credentials(generator_config, ROOT)
    cache = ManualCache(ROOT / ('.toollery/cache-mock' if args.offline_smoke else config['cache_dir']))
    results = []
    with ExitStack() as stack:
        mock = None
        if args.offline_smoke:
            from .mock import MockMessages
            mock = stack.enter_context(MockMessages())
        def show_selection(selected):
            print(f"Selected ({selected['representation']}, {selected['budget']}, {len(selected['selected_names'])}/{selected['full_pool_count']}): {', '.join(selected['selected_names']) or '(none)'}", flush=True)

        def prepare(bridge, session_id):
            if not bridge.sessions[session_id].ready:
                return bridge.prepare(session_id, generator, cache, workers=args.workers, cache_only=args.cache_only,
                                      with_manuals=not args.spec_only,
                                      progress=lambda done, total: print(f'Manuals: {done}/{total}', flush=True))

        bridge = stack.enter_context(DSHBridge(output, representation='spec' if args.spec_only else config['representation'],
                                               budget=config['budget'], k=config['k'], on_selection=show_selection,
                                               enabled=not args.toollery_off, on_snapshot=prepare))
        harness = stack.enter_context(DeepSeekHarness(DeepSeekHarnessConfig(
            provider='deepseek-official', model=config['model'], max_tokens=config['max_tokens'],
            cwd=str(ROOT), runtime_cwd=str(ROOT), profile='sdk-minimal',
            patches=(str(output / 'cordis.patch.json'),),
            dsh_home=str(ROOT / ('.toollery/dsh-cli-mock' if args.offline_smoke else config['dsh_home'])),
            env={'TOOLLERY_BRIDGE_URL': bridge.url, 'TOOLLERY_BRIDGE_TOKEN': bridge.token,
                 **source_environment(config.get('tool_sources'), ROOT)},
            base_url=mock.url if mock else config['base_url'], api_key=key,
            initialize_timeout_seconds=45, request_timeout_seconds=config['request_timeout_seconds'],
            shutdown_timeout_seconds=5,
        )))
        started = time.perf_counter()
        session = harness.start_session()
        session.get_working_directory()  # Materialize agent/snapshot; no model call.
        state = bridge.sessions[session.id]
        print(f'Snapshot: {len(state.pool.tools)} tools; session: {session.id}', flush=True)
        if not args.toollery_off:
            prepare(bridge, session.id)
        atomic_json(output / 'runtime.json', {
            'sdk_version': importlib.metadata.version('deepseek-harness-sdk'),
            'runtime_version': importlib.metadata.version('deepseek-harness-runtime-bin'),
            'session_id': session.id, 'profile': 'sdk-minimal', 'model': config['model'],
            'base_url': mock.url if mock else config['base_url'],
            'snapshot_hash': state.pool.snapshot_hash, 'tool_count': len(state.pool.tools),
            'session_snapshot_and_preparation_seconds': time.perf_counter() - started, 'offline_mock': bool(mock),
            'tool_projection': 'complete_current_subset_without_deferred_updates',
            'tool_scope_contract': 'per_user_turn_v1',
        })
        if args.prepare_only:
            return {'status': 'ready', 'output': str(output), 'preparation': state.preparation}
        print('Ready. Commands: /on; /off; /mode spec|manual|spec_manual; /budget all|top_k; /k N; /tools; /quit', flush=True)

        def turn(prompt):
            started = time.perf_counter()
            result = session.run(prompt)
            record = compact_result(result)
            record['elapsed_seconds'] = time.perf_counter() - started
            results.append(record)
            atomic_json(output / 'turns.json', results)
            report_summary(output, bridge, results, offline=bool(mock))
            for event in result.events:
                if event.get('type') == 'tool/call':
                    data = event['data']
                    print(f"Calling {data['name']}: {data['arguments']}", flush=True)
                elif event.get('type') == 'tool/result':
                    message = event['data']['message']
                    text = '\n'.join(block['text'] for block in message['content'] if block['type'] == 'text')
                    print(('Tool error: ' if message.get('isError') else 'Tool result: ') + text, flush=True)
            print('Assistant: ' + result.final_response, flush=True)
            if result.finish_reason != 'completed':
                end = next((e['data']['reason'] for e in reversed(result.events) if e.get('type') == 'turn/end'), {})
                print('Turn failed: ' + json.dumps(end, ensure_ascii=False), flush=True)
                raise RuntimeError('DSH turn did not finish normally; inspect turns.json')

        if mock:
            cases = [
                ('spec_manual', 'top_k', 'Use add_numbers to add 19 and 23.'),
                ('manual', 'top_k', 'Use celsius_to_fahrenheit to convert 20 Celsius.'),
                ('spec', 'top_k', 'Use multiply_numbers to multiply 6 and 7.'),
                ('spec_manual', 'all', 'Use sha256_text to hash hello.'),
                ('spec', 'top_k', 'zzzxqvv9001'),
                ('spec', 'top_k', 'Use add_numbers to add 19 and 23. [guard_probe]'),
            ]
            for mode, budget, prompt in cases:
                bridge.configure(session.id, representation=mode, budget=budget, k=1)
                turn(prompt)
            atomic_json(output / 'mock-requests.json', mock.requests)
            validation = validate_offline(bridge, results, mock.requests)
            atomic_json(output / 'validation.json', validation)
            print(json.dumps(validation), flush=True)
        elif args.prompt:
            for prompt in args.prompt:
                turn(prompt)
        else:
            while True:
                try:
                    prompt = input('\nYou> ').strip()
                except EOFError:
                    break
                if not prompt:
                    continue
                if prompt == '/quit':
                    break
                if prompt in {'/on', '/off'}:
                    try:
                        bridge.configure(session.id, enabled=prompt == '/on')
                        print('Next turn: Toollery ' + ('ON' if state.enabled else 'OFF'))
                    except Exception as error:
                        print(f'Cannot enable Toollery ({type(error).__name__}); run toollery-dsh prepare if the cache is missing.')
                    continue
                if prompt == '/tools':
                    print('\n'.join(f'{key}: {state.pool.tools[key].documentation["description"]}' for key in state.pool.tool_ids))
                    continue
                if prompt.startswith('/'):
                    try:
                        command, value = prompt.split(maxsplit=1)
                        field = {'/mode': 'representation', '/budget': 'budget', '/k': 'k'}[command]
                        bridge.configure(session.id, **{field: int(value) if field == 'k' else value})
                        print(f'Next turn: {state.representation}, {state.budget}, k={state.k}')
                    except (ValueError, KeyError):
                        print('Invalid command. Use /mode, /budget, /k, /tools or /quit.')
                    continue
                turn(prompt)
    return {'status': 'complete', 'output': str(output), 'turns': len(results)}
