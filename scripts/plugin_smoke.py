"""Offline real-host acceptance; install the bundle in a fresh isolated profile first.

Uses the SDK only to drive the existing Node DSH installation. No model API calls.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
from toollery.storage import atomic_json
from toollery_dsh.configuration import ASSETS
from toollery_dsh.mock import MockMessages


def run(node, cli, home, output):
    output.mkdir(parents=True, exist_ok=True)
    address = output / 'fixture.port'
    address.unlink(missing_ok=True)
    patch = output / 'host-fixture.patch.json'
    atomic_json(patch, [
        *[{'id': name, 'disabled': True} for name in (
            'tool-fs', 'tool-working-directory', 'persistent-bash', 'persistent-pwsh',
            'terminal-bash', 'terminal-pwsh', 'pty')],
        {'id': 'tools', 'config': {'mode': 'native'}},
        {'insert': [
            {'id': 'host-demo-tools', 'name': (ASSETS / 'demo-tools.mjs').as_uri()},
            {'id': 'host-policy-fixture', 'name': (Path(__file__).resolve().parents[1] / 'tests/fixtures/dsh-plugin-host.mjs').as_uri(),
             'config': {'addressFile': str(address)}},
        ]},
    ])
    # Deliberately do not override the default provider model/tool-update catalog.
    with MockMessages(choose_call=lambda prompt, tools: next((
        (name, args) for name, args in (
            ('add_numbers', {'a': 19, 'b': 23}),
            ('celsius_to_fahrenheit', {'celsius': 20}),
            ('sha256_text', {'text': 'hello'}),
            ('uppercase_text', {'text': 'hello'}),
        ) if name in prompt and name in {tool['name'] for tool in tools}), None)) as mock:
        config = DeepSeekHarnessConfig(
            cwd=str(output), runtime_cwd=str(output), profile='sdk-minimal',
            env={'DSH_HOME': str(home)}, base_url=mock.url, api_key='offline-mock',
            initialize_timeout_seconds=45, request_timeout_seconds=45, shutdown_timeout_seconds=8,
        )
        args = (str(node), str(cli), '--profile', 'sdk-minimal', '--patch', str(patch))
        with DeepSeekHarness(config, _launch_args=args) as harness:
            session = harness.start_session()
            session.get_working_directory()
            deadline = time.monotonic() + 15
            while not address.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert address.exists(), 'Host fixture did not start; check installed plugin loading\n' + harness.client._runtime_diagnostics()

            def control(action, **values):
                request = Request(f'http://127.0.0.1:{address.read_text()}',
                                  json.dumps({'action': action, 'id': session.id, **values}).encode(),
                                  {'Content-Type': 'application/json'})
                try:
                    with urlopen(request, timeout=90) as response:
                        return json.load(response)
                except HTTPError as error:
                    body = error.read().decode('utf-8', errors='replace')
                    raise RuntimeError(f'fixture action {action} failed ({error.code}): {body}') from error

            result = control('setup')
            assert result['phase'] == 'ready', result
            directory = home / 'profiles/sdk-minimal/toollery'
            worker = json.loads((directory / 'worker.json').read_text())
            probe = subprocess.run([worker['python'], '-I', '-c',
                'import json, sys; from pathlib import Path; import importlib.metadata as m; import toollery_dsh.worker as w; '
                'print(json.dumps({"version": m.version("toollery-dsh"), "python": sys.version.split()[0], '
                '"private": Path(w.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), '
                '"packages": sorted(d.metadata["Name"] for d in m.distributions())}))'],
                cwd=output, capture_output=True, text=True, check=True, timeout=15)
            installation = json.loads(probe.stdout)
            assert installation['private'] and installation['version'] == worker['version'], installation
            # Desktop's bundled Python intentionally uses venv --without-pip;
            # system-Python profiles may retain pip in the private environment.
            assert installation['packages'] in (['toollery-dsh'], ['pip', 'toollery-dsh']), installation
            atomic_json(directory / 'manual-generator.json', {'provider': 'mock'})
            deadline = time.monotonic() + 15
            while control('status')['phase'] == 'preparing' and time.monotonic() < deadline:
                time.sleep(0.05)
            control('prepare')
            deadline = time.monotonic() + 15
            while control('status')['phase'] != 'ready' and time.monotonic() < deadline:
                time.sleep(0.05)
            status = control('status')
            assert status['phase'] == 'ready', status
            assert status['tool_count'] == status['manuals_valid'] == 12, status
            control('configure', options={'enabled': True, 'representation': 'spec', 'k': 1})
            turns = []
            for prompt in ('Use add_numbers to add 19 and 23.',
                           'Use celsius_to_fahrenheit to convert 20 Celsius.',
                           'multiply'):
                turns.append(session.run(prompt))
            assert control('add-dynamic-tool')['registered']
            control('configure', options={'representation': 'spec_manual'})
            turns.append(session.run('Use uppercase_text to uppercase hello.'))
            refreshed = control('status')
            assert refreshed['tool_count'] == refreshed['manuals_valid'] == 13, refreshed
            refresh_report = json.loads((directory / 'runs/manual-preparation.json').read_text())
            assert refresh_report['generated'] == refresh_report['generation_calls'] == 1, refresh_report
            assert refresh_report['cache_hits'] == 12, refresh_report
            control('configure', options={'enabled': False})
            turns.append(session.run('Use sha256_text to hash hello.'))
            assert all(turn.finish_reason == 'completed' for turn in turns), [turn.finish_reason for turn in turns]
            status = control('status')
            selections = status['turns']
            assert [turn['selected_names'] for turn in selections[:3]] == [
                ['add_numbers'], ['celsius_to_fahrenheit'], []], selections
            assert selections[3]['selected_names'] == ['uppercase_text'], selections
            assert len(selections[-1]['selected_names']) == 12, selections[-1]
            assert all('multiply_numbers' not in turn['selected_names'] for turn in selections)
            assert len(mock.requests) == len(control('streams')) == 9
            for request in mock.requests:
                assert 'Host policy fixture remains active.' in request['system']
                assert not any(tool.get('defer_loading') for tool in request.get('tools', []))
                assert all(tool['name'] != 'multiply_numbers' for tool in request.get('tools', []))
                assert not any(block.get('type', '').startswith('tool_') and block['type'] not in ('tool_use', 'tool_result')
                               for message in request['messages'] for block in message['content'])
            assert [tool['name'] for tool in mock.requests[2]['tools']] == ['celsius_to_fahrenheit']
            assert any(block['type'] == 'tool_use' and block['name'] == 'add_numbers'
                       for message in mock.requests[2]['messages'] for block in message['content'])
            assert any(block['type'] == 'tool_use' and block['name'] == 'uppercase_text'
                       for message in mock.requests[6]['messages'] for block in message['content'])
            atomic_json(output / 'summary.json', {
                'passed': True, 'offline_mock': True, 'turns': 5, 'provider_requests': 9,
                'official_bundle_install': True, 'host_catalog_unchanged': True,
                'worker_installation': installation,
                'disjoint_current_schemas': True, 'history_preserved': True,
                'host_restriction_on_and_off': True, 'host_middleware_dispatch_once': True,
                'dynamic_manual_refresh': {
                    key: refresh_report[key] for key in ('generated', 'generation_calls', 'cache_hits', 'valid')
                },
                'selection_breakdown': [{key: turn[key] for key in ('enabled', 'selected_names', 'eligible_pool_count')} for turn in selections],
            })
            print(json.dumps({'passed': True, 'output': str(output)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', type=Path, required=True)
    parser.add_argument('--dsh-cli', type=Path, required=True)
    parser.add_argument('--dsh-home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.node.resolve(), args.dsh_cli.resolve(), args.dsh_home.resolve(), args.output.resolve())
