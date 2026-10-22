"""Executable ToolTalk supplement: real DSH OFF/all vs ON/spec_manual/top-3.

Uses official simulation and gold prior-context replay, with the explicitly
named exact-text scoring variant. No current-turn gold reaches the model or
manual generator. Raw records are checkpointed per turn for resuming.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
import copy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from toollery.adapters.dsh import DSHBridge
from toollery_dsh.configuration import resolve_generator
from toollery.generation import MockGenerator, make_generator
from toollery.manuals import ManualCache
from toollery.storage import atomic_json, digest, file_digest
from toollery.tooltalk import SCORER, conversation_turns, load_runtime, schemas_for, score_turn
from dsh_demo import load_key, write_patch


class ExecutionService:
    def __init__(self, token):
        self.token, self.tasks = token, {}

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if self.headers.get('Authorization') != 'Bearer ' + owner.token:
                    self.send_response(401); self.end_headers(); return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length < 1000000:
                        raise ValueError('Invalid length')
                    body = json.loads(self.rfile.read(length))
                    task = owner.tasks[body['task_id']]
                    with task['lock']:
                        parameters = copy.deepcopy(body['arguments'])
                        request = {'api_name': body['name'], 'parameters': parameters}
                        try:
                            request, response = task['executor'].execute_tool(body['name'], parameters)
                        except Exception as error:
                            response = {'response': None, 'exception': 'Executor error: ' + type(error).__name__}
                        task['predictions'].append({'role': 'api', 'request': request, **response})
                    data = json.dumps(response).encode()
                    self.send_response(200); self.send_header('Content-Type', 'application/json')
                    self.end_headers(); self.wfile.write(data)
                except Exception:
                    self.send_response(400); self.end_headers()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        return self

    def __exit__(self, *_):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)


class TimedGenerator:
    def __init__(self, generator):
        self.generator, self.calls, self.lock = generator, [], threading.Lock()

    @property
    def contract(self):
        return self.generator.contract

    def generate(self, evidence, count):
        start = time.perf_counter()
        record = {'tool_name': evidence.fields.get('name')}
        try:
            result = self.generator.generate(evidence, count)
            record.update(success=True, usage=result.usage)
            return result
        except Exception as error:
            record.update(success=False, error=type(error).__name__)
            raise
        finally:
            record['wall_seconds'] = time.perf_counter() - start
            with self.lock:
                self.calls.append(record)


def sample_dataset(source, hard_count, smoke):
    paths = sorted((source / 'data/easy').glob('*.json'))
    hard = sorted((source / 'data/tooltalk').glob('*.json'),
                  key=lambda p: digest({'seed': 20261022, 'file': p.name}))[:hard_count]
    if smoke:
        paths, hard = [source / 'data/easy/CurrentWeather-easy.json'], []
    cases, files = [], []
    for split, selected in [('easy', paths), ('hard', hard)]:
        for path in selected:
            conversation = json.loads(path.read_text(encoding='utf-8'))
            files.append({'split': split, 'file': path.relative_to(source).as_posix(), 'sha256': file_digest(path)})
            for turn in conversation_turns(conversation):
                cases.append({**turn, 'case_id': f"{path.stem}-{turn['turn_index']}",
                              'conversation_id': conversation['conversation_id'], 'name': conversation['name'],
                              'split': split, 'metadata': conversation['metadata'], 'user': conversation.get('user', {})})
    return cases, files


def event_measurements(events):
    usage, step_usage = {}, []
    first = []
    user_time = next((e['time'] for e in events if e.get('type') == 'user/message'), None)
    for event in events:
        if event.get('type') != 'assistant/message':
            continue
        data = event['data']
        receipt = data.get('usage', {})
        if receipt:
            step_usage.append(receipt)
        for key, value in receipt.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                usage[key] = usage.get(key, 0) + value
        for part in data.get('stream', []):
            if part.get('type') in ('text-chunks', 'tool-call-chunks') and isinstance(part.get('time0'), (int, float)):
                first.append(part['time0'])
    prompt = sum(usage.get(key, 0) for key in ('inputTokens', 'cacheReadTokens', 'cacheWriteTokens'))
    return {'usage': usage, 'step_usage': step_usage, 'prompt_tokens': prompt,
            'output_tokens': usage.get('outputTokens', 0), 'total_tokens': prompt + usage.get('outputTokens', 0),
            'model_steps': len(step_usage),
            'first_model_output_ms': min(first) - user_time if first and user_time is not None else None}


def stats(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return {'n': 0}
    return {'n': len(values), 'mean': statistics.mean(values), 'median': statistics.median(values),
            'p95': values[min(len(values) - 1, int(len(values) * .95))], 'min': values[0], 'max': values[-1]}


def summarize(output, cases, expected):
    records = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((output / 'turns').glob('*.json'))]
    summary = {'scorer': SCORER, 'expected_turn_runs': expected, 'finished_turn_runs': len(records), 'arms': {}}
    for arm in ('off', 'on'):
        arm_rows = [r for r in records if r['arm'] == arm]
        summary['arms'][arm] = {}
        for split in ('all', 'easy', 'hard'):
            rows = [r for r in arm_rows if split == 'all' or r['split'] == split]
            if not rows:
                continue
            grouped = {}
            for row in rows:
                grouped.setdefault(row['conversation_id'], []).append(row)
            complete_conversations = [group for cid, group in grouped.items()
                                      if len(group) == sum(c['conversation_id'] == cid for c in cases)]
            count = lambda key: sum(r['score'].get(key, 0) for r in rows)
            gold, predictions, matches = count('ground_truths'), count('predictions'), count('matches')
            summary['arms'][arm][split] = {
                'turns': len(rows), 'completed_turns': sum(r['finish_reason'] == 'completed' for r in rows),
                'success_turns': sum(r['success'] for r in rows), 'turn_success_rate': sum(r['success'] for r in rows) / len(rows),
                'success_turns_with_gold_calls': sum(r['success'] and bool(r['gold_calls']) for r in rows),
                'turns_without_gold_calls': sum(not r['gold_calls'] for r in rows),
                'success_turns_without_gold_calls': sum(r['success'] and not r['gold_calls'] for r in rows),
                'complete_conversations': len(complete_conversations),
                'success_conversations': sum(all(r['success'] for r in group) for group in complete_conversations),
                'ground_truth_calls': gold, 'predicted_calls': predictions, 'matched_calls': matches,
                'call_recall': matches / gold if gold else 1, 'call_precision': matches / predictions if predictions else 0,
                'bad_actions': count('bad_actions'), 'actions': count('actions'),
                'call_name_coverage': sum(r['covered_gold_calls'] for r in rows) / gold if gold else 1,
                'gold_turns_with_all_tools_selected': sum(r['all_gold_tools_selected'] for r in rows if r['gold_calls']),
                'turns_with_gold_calls': sum(bool(r['gold_calls']) for r in rows),
                'whole_turn_ms': stats(r['wall_ms'] for r in rows),
                'first_model_output_ms': stats(r.get('first_model_output_ms') for r in rows),
                'retrieval_ms': stats(r.get('retrieval_ms') for r in rows),
                'session_setup_ms': stats(r['session_setup_ms'] for r in rows),
                'prompt_tokens': sum(r['prompt_tokens'] for r in rows),
                'output_tokens': sum(r['output_tokens'] for r in rows),
                'total_tokens': sum(r['total_tokens'] for r in rows),
                'model_steps': sum(r['model_steps'] for r in rows),
                'error_turns': sum(bool(r.get('error')) for r in rows),
                'checker_errors': sum(r.get('checker_errors', 0) for r in rows),
                'projection_checks': sum(r.get('projection_checks', 0) for r in rows),
            }
    paired = {}
    for row in records:
        paired.setdefault(row['case_id'], {})[row['arm']] = row
    pairs = [p for p in paired.values() if len(p) == 2]
    summary['paired'] = {'pairs': len(pairs),
                         'both_success': sum(p['off']['success'] and p['on']['success'] for p in pairs),
                         'both_fail': sum(not p['off']['success'] and not p['on']['success'] for p in pairs),
                         'recovered_on': sum(not p['off']['success'] and p['on']['success'] for p in pairs),
                         'lost_on': sum(p['off']['success'] and not p['on']['success'] for p in pairs),
                         'mean_on_minus_off_ms': statistics.mean(p['on']['wall_ms'] - p['off']['wall_ms'] for p in pairs) if pairs else None}
    losses = [p for p in pairs if p['off']['success'] and not p['on']['success']]
    summary['paired']['lost_on_with_missing_gold_tool'] = sum(not p['on']['all_gold_tools_selected'] for p in losses)
    summary['paired']['lost_on_with_all_gold_tools_available'] = sum(p['on']['all_gold_tools_selected'] for p in losses)
    for label, selected in [('both_success', [p for p in pairs if p['off']['success'] and p['on']['success']]),
                            ('both_success_with_gold_calls', [p for p in pairs if p['off']['success'] and p['on']['success'] and p['on']['gold_calls']])]:
        summary['paired'][label + '_resources'] = {
            arm: {'turns': len(selected), 'whole_turn_ms': stats(p[arm]['wall_ms'] for p in selected),
                  'total_tokens': sum(p[arm]['total_tokens'] for p in selected)} for arm in ('off', 'on')}
    atomic_json(output / 'summary.json', summary)
    return summary


def run(args):
    from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
    output = (ROOT / args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'upstream/ToolTalk'
    apis, Executor = load_runtime(source)
    schemas = schemas_for(apis)
    cases, files = sample_dataset(source, args.hard_count, args.offline_smoke)
    generator_config = json.loads((ROOT / 'examples/generator.command.json').read_text(encoding='utf-8'))
    generator = TimedGenerator(MockGenerator() if args.offline_smoke else make_generator(resolve_generator(generator_config)))
    key = 'local-mock-key' if args.offline_smoke else load_key('TOOLLERY_API_KEY')
    if not args.offline_smoke:
        load_key(generator_config['parameters']['api_key_env'])
    manifest = {'dataset': 'ToolTalk', 'source': 'https://github.com/microsoft/ToolTalk',
                'commit': subprocess.check_output(['git', '-c', 'safe.directory=' + source.as_posix(),
                                                   '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip(),
                'license': 'MIT', 'files': files,
                'database_files': {p.name: file_digest(p) for p in sorted((source / 'data/databases').glob('*.json'))},
                'tools': len(schemas), 'turns': len(cases), 'workers': args.workers, 'scorer': SCORER,
                'arms': {'off': 'full_pool_original_specs', 'on': 'spec_manual_top3_latest_user_query_fixed_per_turn'},
                'offline_mock': args.offline_smoke, 'model': args.model,
                'manual_generator': generator.contract, 'context': 'gold_prior_context_as_system_json',
                'max_model_steps_per_turn': 12,
                'sampling': 'all_28_easy_plus_10_hard_lowest_sha256_seed20261022; no_outcome_filter',
                'sdk': importlib.metadata.version('deepseek-harness-sdk'),
                'runtime': importlib.metadata.version('deepseek-harness-runtime-bin')}
    if (output / 'manifest.json').exists():
        assert json.loads((output / 'manifest.json').read_text(encoding='utf-8')) == manifest, 'Resume manifest mismatch'
    atomic_json(output / 'manifest.json', manifest)
    atomic_json(output / 'tool-schemas.json', schemas)
    plan = [(case, arm) for case in cases for arm in ('off', 'on')]
    random.Random(20261022).shuffle(plan)
    if args.offline_smoke:
        plan.sort(key=lambda task: task[1])
    atomic_json(output / 'plan.json', [{'case_id': c['case_id'], 'arm': a} for c, a in plan])
    with ExitStack() as stack:
        bridge = stack.enter_context(DSHBridge(output / 'bridge', enabled=False, compact_audit=True))
        cache = ManualCache(output / 'manual-cache')
        bridge.capture('startup', schemas)
        cold_path = output / 'manual-first-preparation.json'
        if not cold_path.exists():
            started = time.perf_counter()
            try:
                report = bridge.prepare('startup', generator, cache, workers=args.workers,
                    progress=lambda n, total: print(f'Manuals: {n}/{total}', flush=True))
                atomic_json(cold_path, {'report': report, 'total_ready_seconds': time.perf_counter() - started,
                                       'calls': generator.calls})
            except Exception:
                atomic_json(output / 'manual-failed-attempt.json', {'calls': generator.calls,
                            'wall_seconds': time.perf_counter() - started})
                raise
        warm = []
        for _ in range(3):
            started = time.perf_counter()
            report = bridge.prepare('startup', generator, cache, workers=args.workers, cache_only=True)
            warm.append({'report': report, 'total_ready_seconds': time.perf_counter() - started})
        atomic_json(output / 'manual-warm-preparation.json', warm)
        print('Manual cache ready. Warm checks: 28/28 hits, zero new calls.', flush=True)
        if args.prepare_only:
            return
        service = stack.enter_context(ExecutionService(bridge.token))
        mock = None
        if args.offline_smoke:
            from dsh_mock import MockMessages
            mock = stack.enter_context(MockMessages(choose_call=lambda *_: ('CurrentWeather', {'location': 'New York'})))
        patch_file = output / 'cordis.patch.json'
        write_patch(patch_file, args.model)
        patch = json.loads(patch_file.read_text(encoding='utf-8'))
        patch[-1]['insert'] = [row for row in patch[-1]['insert'] if row['id'] != 'toollery-demo-tools']
        next(r for r in patch if r.get('id') == 'system-prompt')['config']['personaPrefix'] = (
            'You are a helpful assistant in the ToolTalk simulation. Use the available tools to fulfill '
            'the user request. Environment location and timestamp and prior conversation are provided. '
            'Do not invent tools, credentials, missing values or completed actions. Ask for missing information. '
            'Use tool results before answering. When a tool is unavailable, explain the limitation.')
        endpoint_file = output / 'binding-endpoint.json'
        endpoint_file.unlink(missing_ok=True)
        patch[-1]['insert'].insert(0, {'id': 'toollery-tooltalk',
            'name': (ROOT / 'toollery_dsh/assets/dsh/tooltalk-tools.mjs').as_uri(),
            'config': {'schemasFile': str(output / 'tool-schemas.json'), 'endpointFile': str(endpoint_file)}})
        atomic_json(patch_file, patch)
        boot = time.perf_counter()
        harness = stack.enter_context(DeepSeekHarness(DeepSeekHarnessConfig(
            provider='deepseek-official', model=args.model, max_tokens=2048,
            cwd=str(ROOT), runtime_cwd=str(ROOT), profile='sdk-minimal',
            patches=(str(patch_file),), dsh_home=str(output / 'dsh-home'),
            env={'TOOLLERY_BRIDGE_URL': bridge.url, 'TOOLLERY_BRIDGE_TOKEN': bridge.token,
                 'TOOLTALK_EXECUTOR_URL': service.url},
            base_url=mock.url if mock else 'https://api.deepseek.com/anthropic', api_key=key,
            initialize_timeout_seconds=120, request_timeout_seconds=180, shutdown_timeout_seconds=5)))
        harness.start_session().get_working_directory()
        atomic_json(output / 'startup.json', {'harness_boot_and_initial_session_seconds': time.perf_counter() - boot})
        endpoint = json.loads(endpoint_file.read_text(encoding='utf-8'))['url']
        setup_lock = threading.Lock()

        def one(case, arm):
            target = output / 'turns' / (case['case_id'] + '-' + arm + '.json')
            if target.exists():
                return json.loads(target.read_text(encoding='utf-8'))
            task_id = case['case_id'] + '-' + arm
            executor = Executor(init_database_dir=str(source / 'data/databases'))
            executor.init_conversation_state(case['metadata'], copy.deepcopy(case['api_history']), case['user'])
            service.tasks[task_id] = {'executor': executor, 'predictions': [], 'lock': threading.Lock()}
            started = time.perf_counter()
            with setup_lock:
                session = harness.start_session()
                session.get_working_directory()
                state, ready = bridge.sessions[session.id], bridge.sessions['startup']
                assert state.pool.snapshot_hash == ready.pool.snapshot_hash
                # Immutable shared corpus/index; per-session selection state remains isolated.
                state.searches, state.preparation, state.ready = ready.searches, ready.preparation, True
                bridge.configure(session.id, enabled=arm == 'on', representation='spec_manual', k=3)
                binding = {'session_id': session.id, 'task_id': task_id,
                           'metadata': case['metadata'], 'history': case['history']}
                with urlopen(Request(endpoint, data=json.dumps(binding).encode(),
                                     headers={'Authorization': 'Bearer ' + bridge.token, 'Content-Type': 'application/json'}), timeout=15) as response:
                    assert response.status == 200
            setup_ms = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            error, events, finish, answer = None, [], None, ''
            try:
                result = session.run(case['query'])
                events, finish, answer = result.events, result.finish_reason, result.final_response
            except Exception as exception:
                error = type(exception).__name__
            wall_ms = (time.perf_counter() - started) * 1000
            predictions = service.tasks[task_id]['predictions']
            calls = [e['data'] for e in events if e.get('type') == 'tool/call']
            # Include calls rejected by DSH schema validation/Toollery guards.
            remaining = copy.deepcopy(predictions)
            for call in calls:
                existing = next((p for p in remaining if p['request']['api_name'] == call['name']), None)
                if existing is not None:
                    remaining.remove(existing)
                else:
                    try:
                        arguments = json.loads(call['arguments']) if isinstance(call['arguments'], str) else call['arguments']
                    except ValueError:
                        arguments = {}
                    predictions.append({'role': 'api', 'request': {'api_name': call['name'], 'parameters': arguments},
                                        'response': None, 'exception': 'DSH_or_guard_rejected_before_execution'})
            score = score_turn(executor, case['gold'], predictions)
            selections = [s for s in bridge.selections if s['session_id'] == session.id]
            selected = selections[-1]['selected_names'] if selections else []
            observations = [o for o in bridge.observations if o['session_id'] == session.id]
            assert all(set(o['model_tool_names']) == set(selected) for o in observations), 'Projection mismatch'
            gold_names = [g['request']['api_name'] for g in case['gold']]
            record = {'case_id': case['case_id'], 'conversation_id': case['conversation_id'], 'name': case['name'],
                      'arm': arm, 'split': case['split'], 'query': case['query'], 'history': case['history'],
                      'session_id': session.id, 'finish_reason': finish, 'error': error, 'final_response': answer,
                      'gold_calls': case['gold'], 'predictions': predictions, 'score': score,
                      'success': score['success'] and finish == 'completed' and error is None,
                      'selected_names': selected, 'covered_gold_calls': sum(n in selected for n in gold_names),
                      'all_gold_tools_selected': all(n in selected for n in gold_names),
                      'retrieval_ms': sum(s['retrieval_ms'] for s in selections),
                      'session_setup_ms': setup_ms, 'wall_ms': wall_ms, 'events': events,
                      'projection_checks': len(observations), 'checker_errors': getattr(executor, 'checker_errors', 0),
                      **event_measurements(events)}
            atomic_json(target, record)
            print(f"{arm} {case['case_id']}: {finish}, success={record['success']}, "
                  f"calls={len(predictions)}, {wall_ms:.0f}ms, tokens={record['total_tokens']}", flush=True)
            return record

        pending = [(c, a) for c, a in plan if not (output / 'turns' / (c['case_id'] + '-' + a + '.json')).exists()]
        if args.limit:
            pending = pending[:args.limit]
        run_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.workers) as workers:
            futures = [workers.submit(one, c, a) for c, a in pending]
            for future in as_completed(futures):
                future.result()
        summary = summarize(output, cases, len(plan))
        atomic_json(output / ('wall-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json'),
                    {'new_turn_runs': len(pending), 'wall_seconds': time.perf_counter() - run_started, 'workers': args.workers})
        if mock:
            atomic_json(output / 'mock-wire.json', mock.requests)
            assert summary['finished_turn_runs'] == 2
            assert all(summary['arms'][arm]['all']['success_turns'] == 1 for arm in ('off', 'on'))
            assert sorted({len(r.get('tools', [])) for r in mock.requests}) == [3, 28]
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='runs/dsh-tooltalk-20261022')
    parser.add_argument('--workers', type=int, choices=range(1, 9), default=8)
    parser.add_argument('--model', default='deepseek-flash')
    parser.add_argument('--hard-count', type=int, default=10)
    parser.add_argument('--limit', type=int, default=0, help='Maximum new turn runs; resume uses existing records')
    parser.add_argument('--offline-smoke', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    run(parser.parse_args())
