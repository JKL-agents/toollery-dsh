"""Verify OFF/ON across real DSH turns using a local scripted model (no API cost)."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
from dsh_demo import write_patch
from dsh_mock import MockMessages
from toollery.adapters.dsh import DSHBridge
from toollery.generation import MockGenerator
from toollery.manuals import ManualCache
from toollery.storage import atomic_json


def main():
    output = ROOT / ('runs/dsh-switch-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True)
    config = json.loads((ROOT / 'examples/dsh/config.json').read_text(encoding='utf-8'))
    patch = output / 'cordis.patch.json'
    write_patch(patch, config['model'])
    cache = ManualCache(ROOT / 'work/toollery-cache-dsh-mock')

    def prepare(bridge, session_id):
        return bridge.prepare(session_id, MockGenerator(), cache, workers=8)

    with MockMessages() as model, DSHBridge(output, enabled=False, k=1, on_snapshot=prepare) as bridge:
        with DeepSeekHarness(DeepSeekHarnessConfig(
            provider='deepseek-official', model=config['model'], max_tokens=config['max_tokens'],
            cwd=str(ROOT), runtime_cwd=str(ROOT), profile='sdk-minimal', patches=(str(patch),),
            dsh_home=str(ROOT / 'work/dsh-home-switch-mock'),
            env={'TOOLLERY_BRIDGE_URL': bridge.url, 'TOOLLERY_BRIDGE_TOKEN': bridge.token},
            base_url=model.url, api_key='local-mock-key',
            initialize_timeout_seconds=45, request_timeout_seconds=120, shutdown_timeout_seconds=5,
        )) as harness:
            session = harness.start_session()
            session.get_working_directory()
            assert not bridge.status(session.id)['retrieval_ready'], 'OFF startup must skip preparation'
            originals = {key: item.documentation for key, item in bridge.sessions[session.id].pool.tools.items()}
            cases = [(False, 'add_numbers', '42'), (True, 'celsius_to_fahrenheit', '68'),
                     (False, 'add_numbers', '42'), (True, 'celsius_to_fahrenheit', '68')]
            breakdown = []
            for enabled, name, expected in cases:
                bridge.configure(session.id, enabled=enabled)
                before = len(model.requests)
                result = session.run(f'Use {name} to calculate the result (19 + 23 or 20 Celsius).')
                assert result.finish_reason == 'completed', result.finish_reason
                assert expected in result.final_response, result.final_response
                selected = bridge.selections[-1]
                calls = [c for c in bridge.calls if c['selection_id'] == selected['selection_id']]
                assert len(calls) == 1 and calls[0]['name'] == name and calls[0]['status'] == 'success', calls
                names = selected['selected_names']
                assert selected['enabled'] is enabled
                assert set(names) == ({name} if enabled else set(originals)), names
                requests = model.requests[before:]
                assert len(requests) == 2, 'One tool call and one final model response expected'
                for request in requests:
                    tools = request['tools']
                    assert {tool['name'] for tool in tools} == set(names)
                    for tool in tools:
                        original = originals[tool['name']]
                        assert tool['description'] == original['description']
                        assert tool['input_schema'] == original['parameters']
                breakdown.append({'enabled': enabled, 'selected_count': len(names),
                                  'tool': name, 'result': expected, 'model_requests': len(requests)})
                print(f"{'ON' if enabled else 'OFF'}: {len(names)}/12 tools; {name} -> {expected}", flush=True)
            for observed in bridge.observations:
                selected = next(item for item in bridge.selections if item['selection_id'] == observed['selection_id'])
                assert set(observed['model_tool_names']) == set(selected['selected_names'])
            atomic_json(output / 'mock-requests.json', model.requests)
            atomic_json(output / 'validation.json', {'passed': True, 'offline_mock': True,
                        'turns': breakdown, 'model_requests': len(model.requests),
                        'off_startup_skips_preparation': True, 'original_schemas_preserved': True})
    print('Passed. Reports: ' + str(output), flush=True)


if __name__ == '__main__':
    main()
