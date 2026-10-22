"""Reproducible ToolRet retrieval / native DSH projection / paid selection run.

Examples (PowerShell, from the repository root):
  & .venv-dsh/Scripts/python.exe scripts/benchmark_dsh_toolret.py --phase sanity
  & .venv-dsh/Scripts/python.exe scripts/benchmark_dsh_toolret.py --phase all --workers 8
  & .venv-dsh/Scripts/python.exe scripts/benchmark_dsh_toolret.py --phase paid --output runs/dsh-toolret-...
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import threading
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from toollery.adapters.dsh import DSHBridge
from toollery.benchmark_dsh import (effective_dataset, presentation_schemas, attach_indexes,
    stratified_sample, distribution, retrieval_rows, aggregate_rows, parse_selection, selection_score)
from toollery_dsh.configuration import resolve_generator
from toollery.generation import make_generator
from toollery.manuals import ManualCache, prepare_manuals
from toollery.retrieval import BM25Index, REPRESENTATIONS, RankedTool, build_documents
from toollery.storage import atomic_json, canonical, digest, file_digest, read_jsonl
from dsh_demo import load_key, write_patch
from dsh_benchmark_transport import RecordingMessages


def append(path, row):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(canonical(row) + '\n')


def read_if(path):
    return list(read_jsonl(path)) if path.exists() else []


def bridge_timing(bridge, result):
    selection = next(row for row in reversed(bridge.selections) if row['turn'] == result['turn'])
    return {'bridge_retrieval_ms': selection['retrieval_ms'],
            'ranked_names': selection['selected_names']}


class MemorySampler:
    def __init__(self):
        import psutil
        self.psutil = psutil
        self.python = psutil.Process()
        self.dsh_pid = None
        self.peaks = defaultdict(int)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.thread.start()

    def sample(self):
        while not self.stop.is_set():
            self.peaks['python_rss_bytes'] = max(self.peaks['python_rss_bytes'], self.python.memory_info().rss)
            if self.dsh_pid:
                try:
                    process = self.psutil.Process(self.dsh_pid)
                    size = sum(p.memory_info().rss for p in [process, *process.children(recursive=True)]
                               if p.is_running())
                    self.peaks['dsh_tree_rss_bytes'] = max(self.peaks['dsh_tree_rss_bytes'], size)
                except self.psutil.Error:
                    pass
            self.stop.wait(.1)

    def close(self):
        self.stop.set(); self.thread.join(2)


def post(url, token, payload):
    request = Request(url, data=canonical(payload).encode(),
                      headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    with urlopen(request, timeout=900) as response:
        return json.load(response)


def new_harness(stack, output, bridge, receiver, schemas, memory, number):
    from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
    launch = output / f'startup-{number}'
    launch.mkdir(exist_ok=True)
    atomic_json(launch / 'schemas.json', schemas)
    patch_file = launch / 'cordis.patch.json'
    write_patch(patch_file, 'deepseek-flash')
    patch = json.loads(patch_file.read_text('utf-8'))
    patch[-1]['insert'] = [row for row in patch[-1]['insert'] if row['id'] != 'toollery-demo-tools']
    patch[-1]['insert'].append({
        'id': 'toollery-toolret-benchmark', 'name': (ROOT / 'toollery_dsh/assets/dsh/toolret-benchmark.mjs').as_uri(),
        'config': {'model': 'deepseek-flash', 'schemasFile': str(launch / 'schemas.json'),
                   'endpointFile': str(launch / 'endpoint.json'),
                   'requestsFile': str(launch / 'native-requests.jsonl')},
    })
    atomic_json(patch_file, patch)
    started = time.perf_counter()
    harness = stack.enter_context(DeepSeekHarness(DeepSeekHarnessConfig(
        provider='deepseek-official', model='deepseek-flash', max_tokens=512,
        cwd=str(ROOT), runtime_cwd=str(ROOT), profile='sdk-minimal', patches=(str(patch_file),),
        dsh_home=str(launch / 'home'), base_url=receiver.url, api_key='local-recording-endpoint',
        env={'TOOLLERY_BRIDGE_URL': bridge.url, 'TOOLLERY_BRIDGE_TOKEN': bridge.token},
        initialize_timeout_seconds=120, request_timeout_seconds=240, shutdown_timeout_seconds=5,
    )))
    memory.dsh_pid = harness.client._proc.pid
    initialized = time.perf_counter()
    session = harness.start_session()
    session.get_working_directory()
    ready = time.perf_counter()
    endpoint = json.loads((launch / 'endpoint.json').read_text('utf-8'))['url']
    status = post(endpoint + '/status', bridge.token, {})
    if status['agent_id'] != session.id:
        raise RuntimeError('Benchmark driver did not capture the SDK agent')
    return endpoint, session, {'runtime_initialize_ms': (initialized - started) * 1000,
        'session_discovery_snapshot_ms': (ready - initialized) * 1000,
        'registration_ms': status['registration_ms'], 'dsh_ready_ms': (ready - started) * 1000}


def prepare(dataset, output, number):
    started = time.perf_counter()
    config = json.loads((ROOT / 'examples/generator.command.json').read_text('utf-8'))
    manuals = prepare_manuals(dataset.pool, make_generator(resolve_generator(config)),
        ManualCache(ROOT / 'work/toollery-cache-development-full'), count=3, cache_only=True,
        progress=lambda done, total, *_: print(f'Cache validation {number}: {done}/{total}', flush=True)
            if done % 2500 == 0 or done == total else None)
    cache_ms = (time.perf_counter() - started) * 1000
    indexes, timing = {}, {}
    for rep in REPRESENTATIONS:
        now = time.perf_counter()
        indexes[rep] = BM25Index(build_documents(dataset.pool, rep, manuals.records))
        timing[rep] = (time.perf_counter() - now) * 1000
    atomic_json(output / f'preparation-{number}.json', {'manual_report': manuals.report,
        'cache_validation_ms': cache_ms, 'index_build_ms': timing,
        'indexes': {rep: index.metadata for rep, index in indexes.items()}})
    return indexes, manuals.report, {'cache_validation_ms': cache_ms, 'index_build_ms': timing}


def local_retrieval(dataset, meta, indexes, output, sanity):
    queries = dataset.queries[:5] if sanity else dataset.queries
    results, metrics = {}, {}
    path = output / 'retrieval.jsonl'
    if path.exists():
        path.unlink()
    for rep, index in indexes.items():
        for query in queries[:20]:
            index.search(query.text)
        runs = {}
        timing = []
        for repeat in range(3):
            started = time.perf_counter()
            for query in queries:
                now = time.perf_counter()
                ranked = index.search(query.text, limit=10)
                elapsed = (time.perf_counter() - now) * 1000
                timing.append(elapsed)
                if repeat == 0:
                    runs[query.query_id] = ranked
                append(path, {'representation': rep, 'pass': repeat + 1, 'query_id': query.query_id,
                              'retrieval_ms': elapsed,
                              **({'ranked': [{'id': hit.tool_id, 'score': hit.score} for hit in ranked]}
                                 if repeat == 0 else {})})
            print(f'Retrieval {rep} pass {repeat + 1}/3: {len(queries)} queries in {time.perf_counter()-started:.2f}s', flush=True)
        rows = retrieval_rows(runs, dataset)
        results[rep] = runs
        metrics[rep] = {'breakdown': aggregate_rows(rows, meta, dataset.qrels),
                        'retrieval_ms': distribution(timing)}
        atomic_json(output / f'metrics-{rep}.json', metrics[rep])
        atomic_json(output / f'per-query-{rep}.json', rows)
    comparisons = {}
    for rep in ('manual', 'spec_manual'):
        comparisons[rep] = {}
        for k in (1, 3, 5, 10):
            recovered, regressed, gain, loss = [], [], [], []
            for query in queries:
                qid = query.query_id
                positives = set(dataset.qrels[qid])
                before = {hit.tool_id for hit in results['spec'][qid][:k]} & positives
                after = {hit.tool_id for hit in results[rep][qid][:k]} & positives
                if not before and after: recovered.append(qid)
                if before and not after: regressed.append(qid)
                if len(after) > len(before): gain.append(qid)
                if len(after) < len(before): loss.append(qid)
            comparisons[rep][str(k)] = {'recovered': recovered, 'regressed': regressed,
                                       'more_positives': gain, 'fewer_positives': loss}
    atomic_json(output / 'retrieval-comparisons.json', comparisons)
    return results


def projected_wire(schemas):
    return [{'name': tool['name'], 'description': tool['description'], 'input_schema': tool['parameters']}
            for tool in schemas]


def resume_retrieval(dataset, output):
    """Reuse only a complete three-pass artifact; keep its original timings."""
    query_ids = {query.query_id for query in dataset.queries}
    expected = {(rep, repeat, qid) for rep in REPRESENTATIONS
                for repeat in (1, 2, 3) for qid in query_ids}
    seen, runs = set(), {rep: {} for rep in REPRESENTATIONS}
    for row in read_if(output / 'retrieval.jsonl'):
        key = row['representation'], row['pass'], row['query_id']
        if key in seen or key not in expected:
            raise ValueError('Duplicate or unexpected retrieval record: ' + str(key))
        seen.add(key)
        if row['pass'] == 1:
            ranked = [RankedTool(hit['id'], hit['score']) for hit in row['ranked']]
            if any(hit.tool_id not in dataset.pool.tools for hit in ranked):
                raise ValueError('Retrieval references a tool outside the snapshot')
            runs[row['representation']][row['query_id']] = ranked
    if seen != expected:
        raise ValueError('Cannot resume incomplete retrieval artifacts')
    for rep in REPRESENTATIONS:
        for filename in (f'metrics-{rep}.json', f'per-query-{rep}.json'):
            if not (output / filename).is_file():
                raise ValueError('Missing retrieval artifact: ' + filename)
    print('Reusing complete retrieval: 3 representations x 3 timing passes; original timings retained', flush=True)
    return runs


def native_projection(endpoint, bridge, dataset, mapping, schemas, runs, output, sanity):
    import tiktoken
    encoding = tiktoken.get_encoding('cl100k_base')
    reverse = {value: key for key, value in mapping.items()}
    by_name = {tool['name']: tool for tool in schemas}
    queries = dataset.queries[:5] if sanity else dataset.queries
    schema_stats = {}
    # Exact tokenizer count for serialized provider schemas; this is NOT the DeepSeek tokenizer.
    full_wire = projected_wire(schemas)
    full_text = canonical(full_wire)
    baseline = {'tool_count': len(schemas), 'schema_utf8_bytes': len(full_text.encode()),
        'cl100k_schema_token_estimate': len(encoding.encode(full_text, disallowed_special=())),
        'tokenizer': 'cl100k_base (surrogate, not billed DeepSeek tokens)'}
    atomic_json(output / 'all-tools-schema-baseline.json', baseline)
    path = output / 'native-projection.jsonl'
    done = {row['id'] for row in read_if(path)}
    tasks = [{'id': f'{query.query_id}/{rep}/k{k}', 'query': query.text,
              'representation': rep, 'k': k, 'query_id': query.query_id}
             for rep in REPRESENTATIONS for query in queries for k in (3, 10)]
    remaining = [task for task in tasks if task['id'] not in done]
    started = time.perf_counter()
    # Warm up the complete path; excluded from timing distributions.
    warm = {**tasks[0], 'id': 'warmup'}
    post(endpoint + '/batch', bridge.token, {'tasks': [warm]})
    for offset in range(0, len(remaining), 20):
        batch = remaining[offset:offset+20]
        # Concurrency=1 for latency. Node serializes assemblies; full provider calls also run one at a time.
        for task in batch:
            now = time.perf_counter()
            result = post(endpoint + '/batch', bridge.token, {'tasks': [task]})[0]
            result['http_roundtrip_ms'] = (time.perf_counter() - now) * 1000
            result.update(bridge_timing(bridge, result))
            wire = json.loads(result.pop('text'))
            expected = {mapping[hit.tool_id] for hit in runs[task['representation']][task['query_id']][:task['k']]}
            if set(result['selected_names']) != expected or set(wire['tool_names']) != expected:
                raise RuntimeError('Native DSH projection disagrees with core ranking: ' + task['id'])
            if result['ranked_names'] != [mapping[hit.tool_id] for hit in runs[task['representation']][task['query_id']][:task['k']]]:
                raise RuntimeError('Bridge ranked order disagrees with core ranking: ' + task['id'])
            # The original dataset has one empty query. DSH drops empty text
            # messages; retain that zero-result case without inventing input.
            expected_messages = int(bool(task['query'].strip()))
            if wire['deferred_tools'] or wire['message_count'] != expected_messages:
                raise RuntimeError('Unexpected deferred tools or conversation history')
            selected_schemas = [by_name[name] for name in wire['tool_names']]
            projected = projected_wire(selected_schemas)
            if digest(projected) != wire['tools_hash']:
                raise RuntimeError('Provider changed the projected original documentation')
            text = canonical(projected)
            estimate = len(encoding.encode(text, disallowed_special=()))
            append(path, {**task, **result, 'wire': wire,
                'selected_original_ids': [reverse[name] for name in result['selected_names']],
                'cl100k_schema_token_estimate': estimate,
                'schema_byte_reduction_vs_all': 1 - wire['schema_utf8_bytes'] / baseline['schema_utf8_bytes']})
        count = min(offset + len(batch) + len(done), len(tasks))
        if offset % 200 == 0 or count == len(tasks):
            print(f'Native projection: {count}/{len(tasks)}; elapsed {time.perf_counter()-started:.1f}s', flush=True)
    # OFF is assembled and projected locally once, never sent to the paid endpoint.
    off = post(endpoint + '/batch', bridge.token, {'tasks': [{
        'id': 'off-all-baseline', 'query': queries[0].text, 'representation': 'spec', 'k': 10, 'off': True}]})[0]
    wire = json.loads(off.pop('text'))
    if len(wire['tool_names']) != len(schemas) or wire['tools_hash'] != digest(full_wire):
        raise RuntimeError('Toollery OFF did not expose the complete original pool')
    atomic_json(output / 'off-all-projection.json', {**off, 'wire': wire, 'paid': False})
    rows = read_if(path)
    groups = defaultdict(list)
    for row in rows: groups[f"{row['representation']}/k{row['k']}"].append(row)
    summary = {}
    for group, items in groups.items():
        summary[group] = {'n': len(items), **{metric: distribution([row[metric] for row in items])
            for metric in ('bridge_retrieval_ms', 'assembly_ms', 'request_ready_ms', 'provider_ms', 'http_roundtrip_ms', 'total_ms',
                           'cl100k_schema_token_estimate', 'schema_byte_reduction_vs_all')},
            'schema_utf8_bytes': distribution([row['wire']['schema_utf8_bytes'] for row in items])}
    atomic_json(output / 'native-projection-summary.json', summary)


def paid_selection(endpoint, bridge, receiver, dataset, meta, mapping, sample, output, workers, limit=None):
    query_by_id = {query.query_id: query for query in dataset.queries}
    reverse = {value: key for key, value in mapping.items()}
    path = output / 'paid-selection.jsonl'
    existing = read_if(path)
    # Resume completed logical requests only; failed attempts remain in the audit.
    done = {row['id'] for row in existing if row['transport_ok']}
    tasks = [{'id': f'{qid}/{rep}/k{k}', 'query_id': qid, 'query': query_by_id[qid].text,
              'representation': rep, 'k': k}
             for qid in sample for rep in REPRESENTATIONS for k in (3, 10)]
    remaining = [task for task in tasks if task['id'] not in done]
    if limit is not None:
        remaining = remaining[:limit]
    receiver.paid = True
    started = time.perf_counter()
    attempts = 0
    for offset in range(0, len(remaining), workers):
        batch = remaining[offset:offset+workers]
        pending = batch
        for attempt in range(3):
            if not pending: break
            results = post(endpoint + '/batch', bridge.token, {'tasks': pending})
            retry = []
            for task, result in zip(pending, results):
                attempts += 1
                result.update(bridge_timing(bridge, result))
                finish = result.get('finish')
                transport_ok = finish == 'stop' or (isinstance(finish, dict) and finish.get('kind') in ('stop', 'completed'))
                ids, error = parse_selection(result.get('text', ''), result['selected_names'])
                predicted = [reverse[name] for name in ids if name in reverse]
                # Protocol, parse and out-of-pool errors score zero, retaining every original attempt.
                score = selection_score(predicted if transport_ok and error is None else [], dataset.qrels[task['query_id']])
                row = {**task, **result, 'attempt': attempt + 1, 'transport_ok': transport_ok,
                       'parse_error': error, 'predicted_original_ids': predicted, **score}
                append(path, row)
                if not transport_ok and attempt < 2: retry.append(task)
            pending = retry
        completed = min(offset + len(batch) + len(done), len(tasks))
        print(f'Paid selection: {completed}/{len(tasks)} logical requests; {attempts} new attempts; {time.perf_counter()-started:.1f}s', flush=True)
    receiver.paid = False
    records = read_if(path)
    latest = {row['id']: row for row in records}
    logical = list(latest.values())
    groups = defaultdict(list)
    for row in logical:
        for group in (f"{row['representation']}/k{row['k']}",
                      f"{row['representation']}/k{row['k']}/category/{meta[row['query_id']]['category']}",
                      f"{row['representation']}/k{row['k']}/source/{meta[row['query_id']]['source_dataset']}",
                      f"{row['representation']}/k{row['k']}/positives/" + ('single' if len(dataset.qrels[row['query_id']]) == 1 else 'multi')):
            groups[group].append(row)
    summary = {}
    for group, items in groups.items():
        usage = defaultdict(float)
        for row in items:
            for key, value in row.get('usage', {}).items():
                if isinstance(value, (int, float)): usage[key] += value
        summary[group] = {'n': len(items),
            **{key: statistics.fmean(row[key] for row in items) for key in ('precision', 'recall', 'f1', 'success', 'complete')},
            'transport_failures': sum(not row['transport_ok'] for row in items),
            'parse_or_scope_failures': sum(row['parse_error'] is not None for row in items),
            'actual_dsh_usage': dict(usage),
            **{key: distribution([row[key] for row in items if row.get(key) is not None])
               for key in ('bridge_retrieval_ms', 'assembly_ms', 'ttft_ms', 'provider_ms', 'request_ready_ms', 'queue_ms', 'total_ms')}}
    all_usage = defaultdict(float)
    for row in records:
        for key, value in row.get('usage', {}).items():
            if isinstance(value, (int, float)): all_usage[key] += value
    atomic_json(output / 'paid-summary.json', {'logical_requests': len(logical), 'attempt_records': len(records),
        'new_wall_seconds': time.perf_counter() - started, 'new_attempts': attempts,
        'new_attempts_per_second': attempts / max(time.perf_counter() - started, .001),
        'workers': workers, 'actual_dsh_usage_all_attempts': dict(all_usage), 'groups': summary})


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--phase', choices=('sanity', 'local', 'paid', 'all'), default='all')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--paid-limit', type=int, help='Incremental paid preflight; completed IDs are reused on resume')
    parser.add_argument('--resume-retrieval', action='store_true',
                        help='Reuse a complete existing three-pass retrieval artifact after interruption')
    args = parser.parse_args()
    if not 1 <= args.workers <= 8: parser.error('workers must be 1..8')
    output = (args.output or ROOT / 'runs' / ('dsh-toolret-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))).resolve()
    output.mkdir(parents=True, exist_ok=True)
    print('Output: ' + str(output), flush=True)
    memory = MemorySampler()
    if (output / 'memory.json').exists():
        for key, value in json.loads((output / 'memory.json').read_text('utf-8')).items():
            memory.peaks[key] = max(memory.peaks[key], value)
    try:
        dataset, meta, exclusions = effective_dataset(ROOT)
        schemas, mapping = presentation_schemas(dataset.pool)
        sample, strata = stratified_sample(dataset, meta)
        atomic_json(output / 'mapping.json', mapping)
        atomic_json(output / 'sample.json', {'seed': 20261021, 'query_ids': sample, 'strata': strata})
        code_files = [path for directory in ('toollery', 'scripts', 'toollery_dsh/assets/dsh')
                      for path in (ROOT / directory).rglob('*') if path.suffix in ('.py', '.mjs') and '__pycache__' not in path.parts]
        manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'phase': args.phase,
            'tool_count': len(schemas), 'query_count': len(dataset.queries), 'snapshot_hash': dataset.pool.snapshot_hash,
            'provenance': dataset.provenance, 'exclusions': exclusions, 'code_hashes': {str(path.relative_to(ROOT)): file_digest(path) for path in code_files},
            'versions': {name: importlib.metadata.version(name) for name in ('deepseek-harness-sdk', 'deepseek-harness-runtime-bin', 'psutil', 'tiktoken')},
            'python': platform.python_version(), 'platform': platform.platform(), 'logical_cpus': os.cpu_count(),
            'model': 'deepseek-flash', 'workers': args.workers, 'query_input': 'query field only',
            'candidate_pool': 'global complete effective development subset', 'manual_count': 3,
            'presentation': 'Order-preserving anonymous unique name; raw canonical documentation as description; empty nonexecuting argument schema',
            'stateless': 'Real DSH inbox claim + native assembly + native llm service/provider; no conversation loop or tool execution',
            'latency_protocol': '3 complete pure BM25 passes after warmup, 1 complete native pass per k; all native measurements concurrency=1',
            'token_estimates': 'cl100k_base serialized schema only; not DeepSeek tokenizer or billed tokens',
            'paid_all_baseline': False}
        manifest_file = output / 'manifest.json'
        if manifest_file.exists():
            previous = json.loads(manifest_file.read_text('utf-8'))
            for field in ('snapshot_hash', 'model', 'manual_count', 'query_count', 'tool_count'):
                if previous[field] != manifest[field]:
                    raise ValueError('Resume manifest mismatch: ' + field)
        else:
            atomic_json(manifest_file, manifest)
        append(output / 'invocations.jsonl', manifest)
        key = load_key('TOOLLERY_API_KEY') if args.phase in ('all', 'paid') else None
        startup_rows = json.loads((output / 'startup.json').read_text('utf-8')) if (output / 'startup.json').exists() else []
        first_number = len(startup_rows) + 1
        for number in range(first_number, first_number + (3 if args.phase in ('all', 'local') else 1)):
            indexes, preparation, timing = prepare(dataset, output, number)
            with ExitStack() as stack:
                receiver = stack.enter_context(RecordingMessages(output, key=key))
                def on_snapshot(bridge, sid):
                    attach_indexes(bridge, sid, indexes, mapping, preparation)
                bridge = stack.enter_context(DSHBridge(output / f'bridge-{number}', compact_audit=True, on_snapshot=on_snapshot))
                endpoint, session, startup = new_harness(stack, output, bridge, receiver, schemas, memory, number)
                startup_rows.append({**timing, **startup})
                atomic_json(output / 'startup.json', startup_rows)
                print(f'Startup {number}: ready, {len(schemas)} tools; {startup["dsh_ready_ms"]:.0f}ms DSH + cache/index preparation', flush=True)
                if number != first_number: continue
                if args.phase in ('sanity', 'local', 'all'):
                    runs = (resume_retrieval(dataset, output) if args.resume_retrieval
                            else local_retrieval(dataset, meta, indexes, output, args.phase == 'sanity'))
                    native_projection(endpoint, bridge, dataset, mapping, schemas, runs, output, args.phase == 'sanity')
                if args.phase in ('all', 'paid'):
                    paid_selection(endpoint, bridge, receiver, dataset, meta, mapping, sample, output, args.workers, args.paid_limit)
        atomic_json(output / 'completion.json', {'status': 'partial' if args.paid_limit else 'complete', 'phase': args.phase,
            'completed_at': datetime.now(timezone.utc).isoformat(), 'memory_peaks': dict(memory.peaks),
            'new_manual_generation_tokens': 0})
        print('Completed: ' + str(output), flush=True)
    finally:
        memory.close()
        atomic_json(output / 'memory.json', dict(memory.peaks))


if __name__ == '__main__':
    main()
