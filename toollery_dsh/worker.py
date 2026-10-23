"""SDK-free worker owned by a DSH plugin. Stdin EOF ends the child process."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import subprocess
import os
import secrets
import time

from toollery.adapters.dsh import DSHBridge, StaleRefresh, diff_pools, snapshot_pool
from toollery.data import ToolPool, ToolRecord
from toollery.manuals import ManualCache, PreparationError, cache_preflight
from toollery.retrieval import BM25Index, ToolSearch, build_documents
from toollery.storage import atomic_json, canonical, digest
from .configuration import (default_generator, read_json, resolve_generator,
                            configured_generator, load_generator_credentials)
from .onboarding import builtin, manual_key, provider_view, save_provider


def initialize(directory):
    home = Path(directory).resolve()
    home.mkdir(parents=True, exist_ok=True)
    templates = {
        'manual-generator.json': json.dumps(default_generator(), indent=2) + '\n',
        'settings.json': json.dumps({'representation': 'spec_manual', 'budget': 'top_k',
                                     'k': 3, 'workers': 8, 'enabled': True,
                                     'refresh_max_misses': 15}, indent=2) + '\n',
        '.env.example': '# Manual generation only. Chat credentials belong to DSH.\nTOOLLERY_MANUAL_API_KEY=\n',
    }
    for name, content in templates.items():
        path = home / name
        if not path.exists():
            path.write_text(content, encoding='utf-8')
    return home


class WorkerBridge(DSHBridge):
    def __init__(self, directory):
        self.home = initialize(directory)
        settings = read_json(self.home / 'settings.json')
        self.auto_enable = settings.get('enabled', True)
        self.workers = settings.get('workers', 8)
        self.refresh_max_misses = settings.get('refresh_max_misses', 15)
        if (not isinstance(self.auto_enable, bool) or type(self.workers) is not int or not 1 <= self.workers <= 8
                or type(self.refresh_max_misses) is not int or not 0 <= self.refresh_max_misses <= 1000):
            raise ValueError('Invalid worker settings')
        self.phases = {}
        self.desired_enabled = {}
        self.released = set()
        self.process_lock = threading.Lock()
        self.processes = set()
        self.closing = False
        self.jobs = {}
        self.refresh_jobs = {}
        self.pending_refreshes = {}
        self.discovery_jobs = {}
        self.discovery_catalog = self.home / 'discovery-catalog.json'
        self.preparation_lock = threading.Lock()
        super().__init__(self.home / 'runs', enabled=False, state_dir=self.home / 'sessions',
                         representation=settings.get('representation', 'spec_manual'),
                         budget=settings.get('budget', 'top_k'), k=settings.get('k', 3),
                         handlers={'/prepare': self.request_preparation, '/release': self.release,
                                   '/generator': self.generator_settings, '/refresh': self.request_refresh,
                                   '/refresh-status': self.refresh_status, '/discover': self.request_discovery,
                                   '/discover-status': self.discovery_status})

    def generator_settings(self, payload):
        with self._lock:
            if 'settings' in payload:
                if any(job.is_alive() for job in (*self.jobs.values(),
                        *[item['thread'] for item in self.refresh_jobs.values()],
                        *[item['thread'] for item in self.discovery_jobs.values()])):
                    return {'ok': False, 'error': 'Wait for the current cache check or generation to finish before saving.'}
                try:
                    view = save_provider(self.home, payload['settings'])
                except ValueError as error:
                    return {'ok': False, 'error': str(error)}
                # Saving settings only rechecks the cache. It never authorizes
                # generation and does not require a Desktop restart.
                for session_id, state in list(self.sessions.items()):
                    state.enabled = False
                    state.ready = False
                    state.searches = {}
                    self._start(session_id, generate=False)
                return {'ok': True, 'generator': view}
            return {'ok': True, 'generator': provider_view(self.home)}

    def capture(self, session_id, schemas, discovery=None):
        result = super().capture(session_id, schemas, discovery)
        with self._lock:
            if session_id not in self.phases:
                # A persisted ON setting is not permission to serve before the
                # current snapshot's complete cache and indexes are checked.
                state = self.sessions[session_id]
                self.desired_enabled[session_id] = state.enabled if state.revision else self.auto_enable
                state.enabled = False
                self.phases[session_id] = {'phase': 'preparing', 'completed': 0, 'total': len(schemas)}
                self._start(session_id, generate=False)
        return result

    def configure(self, session_id, **options):
        with self._lock:
            super().configure(session_id, **options)
            if options.get('enabled') is not None:
                self.desired_enabled[session_id] = options['enabled']

    def release(self, payload):
        session_id = payload['session_id']
        with self._lock:
            self.released.add(session_id)
            self._forget_if_idle(session_id)
        return {'ok': True}

    def _forget_if_idle(self, session_id):
        current = threading.current_thread()
        threads = ([self.jobs.get(session_id)] if self.phases.get(session_id, {}).get('phase') == 'preparing' else []) + [
            item['thread'] for mapping in (self.refresh_jobs, self.discovery_jobs)
            for item in mapping.values() if item['session_id'] == session_id
            and item['phase'] in {'preparing', 'refreshing'}]
        if session_id in self.released and not any(
                thread and thread is not current and thread.is_alive() for thread in threads):
            self._forget(session_id)

    def _forget(self, session_id):
        for mapping in (self.sessions, self.phases, self.desired_enabled, self.jobs, self.pending_refreshes):
            mapping.pop(session_id, None)
        for mapping in (self.refresh_jobs, self.discovery_jobs):
            for key in [key for key, item in mapping.items() if item['session_id'] == session_id]:
                mapping.pop(key)
        self.released.discard(session_id)
        self.selections[:] = [r for r in self.selections if r['session_id'] != session_id]
        self.calls[:] = [r for r in self.calls if r['session_id'] != session_id]
        self.observations[:] = [r for r in self.observations if r['session_id'] != session_id]

    def _start(self, session_id, *, generate):
        self.phases[session_id] = self._progress_state(
            {'phase': 'preparing', 'completed': 0,
             'total': len(self.sessions[session_id].pool.tools),
             'generating': generate})
        job = threading.Thread(target=self._prepare, args=(session_id, generate), daemon=True)
        self.jobs[session_id] = job
        job.start()

    def request_preparation(self, payload):
        session_id = payload['session_id']
        # An explicit action is required for paid requests, even with saved keys.
        if payload.get('allow_generation') is not True:
            raise ValueError('Explicit generation permission required')
        with self._lock:
            state = self.sessions[session_id]
            pending = self.pending_refreshes.get(session_id)
            if not pending:
                if self.jobs.get(session_id) and self.jobs[session_id].is_alive():
                    return {'accepted': False, 'reason': 'preparation_in_progress'}
                if state.ready and all(mode in state.searches for mode in ('spec', 'manual', 'spec_manual')):
                    return {'accepted': False, 'reason': 'already_ready'}
                self._start(session_id, generate=True)
        if pending:
            return self.request_refresh({**pending, 'allow_generation': True})
        return {'accepted': True}

    def _configured_generator(self, *, generate):
        raw = read_json(self.home / 'manual-generator.json')
        generation = resolve_generator(raw)
        if generate and not builtin(raw):
            load_generator_credentials(generation, self.home)
        if generate and builtin(raw) and not manual_key(self.home, raw):
            raise ValueError('Missing manual API key')
        generator = configured_generator(generation, self.home, require_executable=generate)
        if hasattr(generator, 'runner'):
            generator.runner = self.run_generation
        return generator

    def request_refresh(self, payload):
        session_id = payload['session_id']
        schemas = payload.get('schemas')
        target = snapshot_pool(schemas)
        expected_revision = payload.get('pool_revision')
        expected_hash = payload.get('snapshot_hash')
        max_misses = payload.get('max_auto_tools', self.refresh_max_misses)
        if type(max_misses) is not int or not 0 <= max_misses <= 1000:
            raise ValueError('Invalid refresh threshold')
        with self._lock:
            state = self.sessions[session_id]
            if session_id in self.released:
                raise ValueError('Session has been released')
            if (expected_revision is not None and expected_revision != state.pool_revision
                    or expected_hash is not None and expected_hash != state.pool.snapshot_hash):
                raise StaleRefresh()
            diff = diff_pools(state.pool, target)
            if target.snapshot_hash == state.pool.snapshot_hash:
                self.pending_refreshes.pop(session_id, None)
                phase = self.phases.get(session_id, {})
                # A source can withdraw its pending change before approval.
                # Clear the obsolete approval, without hiding an active job.
                if phase.get('phase') in {'approval_required', 'refresh_failed', 'stale_refresh'}:
                    phase.update(phase='ready' if state.ready else 'needs_preparation',
                                 error='', missing=0, pending_tool_count=None,
                                 completed=len(target.tools), total=len(target.tools), eta_seconds=None)
                return {'status': 'unchanged', 'snapshot_hash': state.pool.snapshot_hash,
                        'pool_revision': state.pool_revision, 'diff': diff, 'missing': 0}
            base_revision, base_hash = state.pool_revision, state.pool.snapshot_hash
            representation = payload.get('representation') or state.representation
            self.validate_options(representation, state.budget, state.k)
            # Approval/retry must target the actual new snapshot, not the old
            # committed pool. Keep immutable schemas and the original CAS base.
            self.pending_refreshes[session_id] = {
                'session_id': session_id,
                'schemas': [target.tools[key].documentation for key in target.tool_ids],
                'snapshot_hash': base_hash, 'pool_revision': base_revision,
                'representation': representation, 'max_auto_tools': max_misses,
            }
        cache = ManualCache(self.home / 'cache')
        if representation == 'spec':
            preflight = {'total': len(target.tools), 'valid': len(target.tools), 'missing': 0,
                         'missing_tool_ids': [], 'failures': []}
        else:
            generator = self._configured_generator(generate=False)
            preflight = cache_preflight(target, generator, cache)
        missing = preflight.get('missing', 0)
        if missing > max_misses and payload.get('allow_generation') is not True:
            with self._lock:
                self.phases[session_id] = {**self.phases.get(session_id, {}),
                                           'phase': 'approval_required', 'completed': 0,
                                           'total': len(target.tools), 'missing': missing,
                                           'pending_tool_count': len(target.tools),
                                           'missing_tool_ids': preflight.get('missing_tool_ids', []),
                                           'max_auto_tools': max_misses,
                                           'error': f'{missing} manuals require explicit generation approval.'}
            return {'status': 'approval_required', 'snapshot_hash': target.snapshot_hash,
                    'pool_revision': base_revision, 'diff': diff, 'missing': missing,
                    'missing_tool_ids': preflight.get('missing_tool_ids', []),
                    'max_auto_tools': max_misses}
        key = (session_id, target.snapshot_hash)
        with self._lock:
            existing = self.refresh_jobs.get(key)
            if existing and existing['thread'].is_alive():
                return {'status': 'accepted', 'job_id': existing['job_id'], 'snapshot_hash': target.snapshot_hash,
                        'pool_revision': base_revision, 'diff': diff, 'missing': missing}
            job_id = target.snapshot_hash
            job = {'job_id': job_id, 'session_id': session_id, 'target_hash': target.snapshot_hash,
                   'phase': 'refreshing', 'completed': 0, 'total': len(target.tools),
                   'missing': missing, 'diff': diff, 'base_revision': base_revision,
                   'base_hash': base_hash, 'representation': representation,
                   '_started_monotonic': time.monotonic(),
                   '_generation_total': missing, '_generation_completed': 0}
            job.update(self._progress_fields(0, len(target.tools), job['_started_monotonic']))
            thread = threading.Thread(target=self._refresh, args=(job, target, payload), daemon=True)
            job['thread'] = thread
            self.refresh_jobs[key] = job
            self.phases[session_id] = self._progress_state(
                {'phase': 'refreshing',
                 'completed': 0, 'total': len(target.tools), 'refresh_job_id': job_id,
                 'missing': missing, '_generation_total': missing, '_generation_completed': 0,
                 '_started_monotonic': time.monotonic()})
            thread.start()
        return {'status': 'accepted', 'job_id': job_id, 'snapshot_hash': target.snapshot_hash,
                'pool_revision': base_revision, 'diff': diff, 'missing': missing}

    def refresh_status(self, payload):
        session_id, job_id = payload['session_id'], payload['job_id']
        with self._lock:
            job = next((item for item in self.refresh_jobs.values()
                        if item['session_id'] == session_id and item['job_id'] == job_id), None)
            if not job:
                raise KeyError('Unknown refresh job')
            result = {key: value for key, value in job.items()
                      if key != 'thread' and not key.startswith('_')}
            if job['phase'] == 'refreshing':
                result.update(self._timing(job))
            return result

    @staticmethod
    def _progress_fields(completed, total, started_monotonic):
        """Return approximate elapsed/remaining time for a preparation job."""
        elapsed = max(0.0, time.monotonic() - started_monotonic)
        rate = completed / elapsed if completed > 0 and elapsed > 0 else None
        if completed > 0 and total > completed:
            eta = max(0.0, elapsed * (total - completed) / completed)
        else:
            eta = None
        return {'elapsed_seconds': elapsed, 'rate_per_second': rate, 'eta_seconds': eta}

    @classmethod
    def _progress_state(cls, state):
        started = state.get('_started_monotonic')
        if started is None:
            started = time.monotonic()
            state['_started_monotonic'] = started
        state.update(cls._timing(state))
        return state

    @classmethod
    def _timing(cls, state):
        # Cached tools finish almost instantly and must not make a single slow
        # generation appear to have near-zero time remaining.
        return cls._progress_fields(
            state.get('_generation_completed', state.get('completed', 0)),
            state.get('_generation_total', state.get('total', 0)),
            state['_started_monotonic'])

    def _generation_progress(self, state, report):
        with self._lock:
            state['_generation_completed'] = state.get('_generation_completed', 0) + report.get('attempted_tools', 0)

    @staticmethod
    def _candidate_pool(candidates):
        if not isinstance(candidates, list) or not candidates or len(candidates) > 256:
            raise ValueError('Candidate batch must contain 1..256 tools')
        normalized = []
        metadata = {}
        seen = set()
        for item in candidates:
            if not isinstance(item, dict):
                raise ValueError('Invalid discovery candidate')
            # A provider may attach executor/source metadata, but Toollery only
            # accepts the model-facing schema and never stores or executes that
            # metadata. The provider retains the mapping from name to executor.
            source = item.get('schema', item)
            if not isinstance(source, dict):
                raise ValueError('Invalid discovery candidate schema')
            schema = {key: source[key] for key in ('name', 'description', 'parameters') if key in source}
            if isinstance(source.get('output'), dict):
                schema['output'] = source['output']
            if not isinstance(schema.get('name'), str) or not schema['name'].strip():
                raise ValueError('Invalid discovery candidate name')
            if ('description' in schema and not isinstance(schema['description'], str)
                    or 'parameters' in schema and not isinstance(schema['parameters'], dict)):
                raise ValueError('Invalid discovery candidate schema')
            tool_id = item.get('tool_id') or item.get('id')
            namespace = item.get('provider_id') or item.get('source_id')
            if tool_id is None and isinstance(namespace, str) and namespace.strip():
                tool_id = f'{namespace.strip()}:{schema["name"]}'
            if tool_id is None:
                tool_id = schema['name']
            if not isinstance(tool_id, str) or not tool_id.strip():
                raise ValueError('Invalid discovery candidate tool_id')
            tool_id = tool_id.strip()
            candidate_ref = item.get('candidate_ref')
            if candidate_ref is not None and (not isinstance(candidate_ref, str) or not candidate_ref.strip()):
                raise ValueError('Invalid discovery candidate_ref')
            if tool_id in seen:
                raise ValueError('Discovery candidates must have unique tool_id values')
            seen.add(tool_id)
            schema = json.loads(canonical(schema))
            normalized.append({'schema': schema, 'tool_id': tool_id, **({'candidate_ref': candidate_ref} if candidate_ref is not None else {})})
            metadata[tool_id] = {
                'tool_id': tool_id,
                'name': schema['name'],
                'candidate_ref': candidate_ref,
                'spec_hash': digest(schema),
            }
        pool = ToolPool([ToolRecord(item['tool_id'], item['schema']) for item in normalized])
        return pool, metadata, normalized

    @staticmethod
    def _candidate_snapshot_hash(pool, metadata):
        return digest([metadata[tool_id] for tool_id in pool.tool_ids])

    def _remember_candidates(self, pool):
        with self._lock:
            catalog = {}
            try:
                value = json.loads(self.discovery_catalog.read_text(encoding='utf-8'))
                if isinstance(value, dict):
                    catalog.update({key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, dict)})
            except (OSError, ValueError, TypeError):
                pass
            catalog.update({tool_id: pool.tools[tool_id].documentation for tool_id in pool.tool_ids})
            atomic_json(self.discovery_catalog, {key: catalog[key] for key in sorted(catalog)})

    def request_discovery(self, payload):
        """Retain a job after a caller timeout; matching retries share it."""
        session_id = payload['session_id']
        pool, options, metadata, normalized = self._discovery_options(payload)
        candidate_snapshot_hash = self._candidate_snapshot_hash(pool, metadata)
        with self._lock:
            contract = (self._configured_generator(generate=False).contract
                        if options['representation'] != 'spec' else None)
            # Permission is a request policy, not part of the preparation
            # identity: a retry after approval must join the same job.
            identity_options = {key: value for key, value in options.items()
                                if key not in {'allow_generation', 'request_id'}}
            key = digest([session_id, candidate_snapshot_hash, identity_options, contract])
            existing = self.discovery_jobs.get(key)
            if existing:
                if existing['thread'].is_alive():
                    return {'status': 'accepted', 'job_id': existing['job_id'],
                            'request_id': existing['request_id'],
                            'candidate_snapshot_hash': candidate_snapshot_hash}
                completed = existing.get('result', {})
                if completed.get('status') == 'ranked':
                    return completed
                if completed.get('status') == 'approval_required' and not options['allow_generation']:
                    return completed
                # A failed preparation is retryable after the caller fixes the
                # provider configuration or transient network/quota issue.
                self.discovery_jobs.pop(key, None)
            request_id = payload.get('request_id') or f'discovery-{secrets.token_hex(12)}'
            job = {'job_id': secrets.token_hex(16), 'request_id': request_id,
                   'session_id': session_id, 'phase': 'preparing',
                   'candidate_snapshot_hash': candidate_snapshot_hash}
            frozen = {**options, 'session_id': session_id,
                      'request_id': request_id, 'candidates': normalized}
            thread = threading.Thread(target=self._discover, args=(job, frozen), daemon=True)
            job['thread'] = thread
            self.discovery_jobs[key] = job
            thread.start()
        return {'status': 'accepted', 'job_id': job['job_id'], 'request_id': job['request_id'],
                'candidate_snapshot_hash': candidate_snapshot_hash}

    def _discover(self, job, payload):
        try:
            result = self.rank_candidates(payload)
        except Exception as error:
            # Provider response text, commands and credentials stay private.
            result = {'status': 'failed', 'error': type(error).__name__}
        with self._lock:
            job.update(phase='completed', result=result)
            self._forget_if_idle(job['session_id'])

    def discovery_status(self, payload):
        with self._lock:
            job = next((item for item in self.discovery_jobs.values()
                        if item['session_id'] == payload['session_id']
                        and item['job_id'] == payload['job_id']), None)
            if not job:
                raise KeyError('Unknown discovery job')
            return {key: value for key, value in job.items() if key != 'thread'}

    def _discovery_options(self, payload):
        prompt = payload.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('A discovery prompt is required')
        pool, metadata, normalized = self._candidate_pool(payload.get('candidates'))
        with self._lock:
            state = self.sessions[payload['session_id']]
            representation = payload.get('representation', state.representation)
            budget = payload.get('budget', state.budget)
            k = payload.get('k', state.k)
            self.validate_options(representation, budget, k)
        max_misses = payload.get('max_auto_tools', self.refresh_max_misses)
        if type(max_misses) is not int or not 0 <= max_misses <= 1000:
            raise ValueError('Invalid discovery refresh threshold')
        return pool, {'prompt': prompt, 'representation': representation, 'budget': budget, 'k': k,
                      'max_auto_tools': max_misses, 'allow_generation': payload.get('allow_generation') is True,
                      'request_id': payload.get('request_id')}, metadata, normalized

    def rank_candidates(self, payload):
        pool, options, metadata, _normalized = self._discovery_options(payload)
        prompt, representation = options['prompt'], options['representation']
        budget, k = options['budget'], options['k']
        request_id = options['request_id'] or f'discovery-{secrets.token_hex(12)}'
        candidate_snapshot_hash = self._candidate_snapshot_hash(pool, metadata)
        self._remember_candidates(pool)
        cache = ManualCache(self.home / 'cache')
        if representation == 'spec':
            searches = {'spec': ToolSearch(pool, BM25Index(build_documents(pool, 'spec')))}
            report = {'ready': True, 'total': len(pool.tools), 'valid': len(pool.tools),
                      'cache_hits': 0, 'generated': 0, 'generation_calls': 0,
                      'missing': 0, 'proxy_query_count': 3, 'representation': 'spec'}
        else:
            with self.preparation_lock:
                generator = self._configured_generator(generate=False)
                preflight = cache_preflight(pool, generator, cache)
                max_misses = options['max_auto_tools']
                if preflight['missing'] > max_misses and not options['allow_generation']:
                    return {'status': 'approval_required', 'candidate_count': len(pool.tools),
                            'request_id': request_id, 'candidate_snapshot_hash': candidate_snapshot_hash,
                            'missing': preflight['missing'], 'missing_tool_ids': preflight['missing_tool_ids'],
                            'max_auto_tools': max_misses, 'catalog_path': str(self.discovery_catalog)}
                try:
                    generator = self._configured_generator(generate=preflight['missing'] > 0)
                    searches, report = self._build_preparation(
                        pool, representation, generator, cache, workers=self.workers,
                        cache_only=preflight['missing'] == 0, with_manuals=True, persist_report=False)
                except PreparationError as error:
                    return {'status': 'failed', 'error': 'manual_preparation_failed',
                            'request_id': request_id, 'candidate_snapshot_hash': candidate_snapshot_hash,
                            'manual_report': error.report}
                report = {**preflight, **report, 'representation': representation}
        ranked = searches[representation].select(prompt, budget=budget, k=k)
        selected_names = [metadata[hit.tool_id]['name'] for hit in ranked]
        selected = [{**metadata[hit.tool_id], 'score': hit.score} for hit in ranked]
        return {
            'status': 'ranked', 'request_id': request_id, 'prompt': prompt, 'representation': representation,
            'budget': budget, 'k': k, 'candidate_count': len(pool.tools),
            'candidate_snapshot_hash': candidate_snapshot_hash,
            'selection': {'method': representation, 'candidate_count': len(pool.tools), 'k': k},
            'selected_names': selected_names,
            'selected': selected,
            'ranked': [{**metadata[hit.tool_id], 'score': hit.score} for hit in ranked],
            'selected_candidates': [pool.tools[hit.tool_id].documentation for hit in ranked],
            'manual_report': report, 'catalog_path': str(self.discovery_catalog),
        }

    def _refresh(self, job, target, payload):
        session_id = job['session_id']
        try:
            with self.preparation_lock:
                representation = job['representation']
                generate = representation != 'spec' and job['missing'] > 0
                generator = self._configured_generator(generate=generate) if representation != 'spec' else None

                def progress(done, total):
                    with self._lock:
                        job.update(completed=done, total=total)
                        job.update(self._timing(job))
                        if session_id in self.phases:
                            self.phases[session_id].update(completed=done, total=total)
                            self.phases[session_id].update(
                                self._timing(self.phases[session_id]))

                def generation_progress(report):
                    self._generation_progress(job, report)
                    self._generation_progress(self.phases[session_id], report)

                target_schemas = [target.tools[tool_id].documentation for tool_id in target.tool_ids]
                result = self.refresh_pool(
                    session_id, target_schemas,
                    generator, ManualCache(self.home / 'cache'), workers=self.workers,
                    cache_only=not generate, expected_pool_revision=job['base_revision'],
                    expected_snapshot_hash=job['base_hash'], progress=progress,
                    representation=representation, generation_progress=generation_progress)
            with self._lock:
                job.update(phase=result['status'], result=result, completed=job['total'])
                job.update(self._progress_fields(job['total'], job['total'], job['_started_monotonic']))
                pending = self.pending_refreshes.get(session_id)
                if pending and snapshot_pool(pending['schemas']).snapshot_hash == target.snapshot_hash:
                    self.pending_refreshes.pop(session_id, None)
                self.phases[session_id] = self._progress_state(
                    {**self.phases.get(session_id, {}), 'phase': 'ready',
                     'completed': job['total'], 'total': job['total'],
                     'report': result.get('report', {}), 'refresh_job_id': job['job_id'],
                     'error': '', 'missing': 0, 'pending_tool_count': None})
        except StaleRefresh as error:
            with self._lock:
                job.update(phase='stale_refresh', error=error.code)
                self.phases[session_id] = self._progress_state(
                    {**self.phases.get(session_id, {}), 'phase': 'stale_refresh',
                     'refresh_job_id': job['job_id']})
        except PreparationError as error:
            with self._lock:
                job.update(phase='failed', error='manual_preparation_failed', report=error.report)
                self.phases[session_id] = self._progress_state(
                    {**self.phases.get(session_id, {}), 'phase': 'refresh_failed',
                     'error': 'Some manuals could not be generated.', 'report': error.report})
        except Exception as error:
            with self._lock:
                job.update(phase='failed', error=f'{type(error).__name__}')
                self.phases[session_id] = self._progress_state(
                    {**self.phases.get(session_id, {}), 'phase': 'refresh_failed',
                     'error': 'Manual refresh failed. Check the model, endpoint and API key.',
                     'refresh_job_id': job['job_id']})
        finally:
            with self._lock:
                job.update(self._timing(job), eta_seconds=None)
                phase = self.phases.get(session_id)
                if phase and phase.get('refresh_job_id') == job['job_id']:
                    phase.update(self._timing(phase), eta_seconds=None)
                self._forget_if_idle(session_id)

    def _prepare(self, session_id, generate):
        try:
            with self.preparation_lock:
                raw = read_json(self.home / 'manual-generator.json')
                generation = resolve_generator(raw)
                if generate and not builtin(raw):
                    load_generator_credentials(generation, self.home)
                if generate and builtin(raw) and not manual_key(self.home, raw):
                    raise ValueError('Missing manual API key')
                generator = configured_generator(generation, self.home, require_executable=generate)
                if hasattr(generator, 'runner'):
                    generator.runner = self.run_generation
                cache = ManualCache(self.home / 'cache')
                if generate:
                    preflight = cache_preflight(self.sessions[session_id].pool, generator, cache)
                    with self._lock:
                        self.phases[session_id].update(
                            _generation_total=preflight['missing'], _generation_completed=0,
                            missing=preflight['missing'])

                def progress(done, total):
                    with self._lock:
                        self.phases[session_id].update(completed=done, total=total)
                        self.phases[session_id].update(self._timing(self.phases[session_id]))

                report = self.prepare(session_id, generator, cache,
                                      workers=self.workers, cache_only=not generate, progress=progress,
                                      generation_progress=lambda report: self._generation_progress(self.phases[session_id], report))
                with self._lock:
                    self.phases[session_id].update(phase='ready', report=report)
                    self.phases[session_id].update(
                        self._progress_fields(self.phases[session_id].get('total', 0),
                                              self.phases[session_id].get('total', 0),
                                              self.phases[session_id]['_started_monotonic']))
                    self.sessions[session_id].enabled = self.desired_enabled[session_id]
                    self._persist(session_id)
        except PreparationError as error:
            with self._lock:
                failures = error.report.get('failures', [])
                self.phases[session_id].update(phase='needs_preparation', report=error.report,
                    missing=error.report['total'] - error.report['valid'],
                    error=('Some manuals could not be generated. Check the model, endpoint, key and provider quota, then retry. Completed manuals are cached.'
                           if generate and failures else 'Missing or stale manuals. Prepare manuals to continue.'))
        except Exception as error:
            # Do not expose provider responses, commands or credentials to Web.
            with self._lock:
                self.phases[session_id].update(phase='needs_preparation',
                    error=f'Manual configuration or preparation failed ({type(error).__name__}). Check the manual model, endpoint and API key, then retry.')
        finally:
            with self._lock:
                self.phases[session_id].update(self._timing(self.phases[session_id]), eta_seconds=None)
                self._forget_if_idle(session_id)

    def run_generation(self, command, *, input, timeout, capture_output, check, **options):
        # The built-in bridge must use this installed worker, even if the profile
        # contains a Python module with the same name or inherits PYTHONPATH.
        # Preserve the generation contract and explicitly configured custom commands.
        value = read_json(self.home / 'manual-generator.json')
        builtin_bridge = (builtin(value)
                          and isinstance(command, list) and len(command) == 3
                          and command[1:] == ['-m', 'toollery_dsh.provider_bridge'])
        if builtin_bridge:
            command = [sys.executable, '-E', '-I', *command[1:]]
            key = manual_key(self.home, value)
            if key:
                # Python startup variables from the host/profile must not leak
                # into the private bridge interpreter.  ``-I`` ignores most of
                # them, but Python builds on Windows can still consult
                # PYTHONHOME while initializing the runtime.  A hostile or
                # stale value can make the child exit before provider_bridge
                # reads stdin, yielding an opaque bridge_error and no request.
                env = {name: value for name, value in os.environ.items()
                       if not name.upper().startswith('PYTHON')}
                env[value.get('parameters', {}).get('api_key_env', 'TOOLLERY_MANUAL_API_KEY')] = key
                options['env'] = env
        # Own direct generator children so unloading the plugin cannot leave
        # our provider bridge running after its worker exits.
        with self.process_lock:
            if self.closing:
                raise RuntimeError('Worker is stopping')
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, **options)
            self.processes.add(process)
        try:
            try:
                stdout, stderr = process.communicate(input, timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        finally:
            with self.process_lock:
                self.processes.discard(process)

    def __exit__(self, *args):
        with self.process_lock:
            self.closing = True
            for process in self.processes:
                if process.poll() is None:
                    process.kill()
        return super().__exit__(*args)

    def status(self, session_id):
        with self._lock:
            phase = self.phases.get(session_id, {})
            public_phase = {key: value for key, value in phase.items() if not key.startswith('_')}
            if phase.get('phase') in {'preparing', 'refreshing'}:
                public_phase.update(self._timing(phase))
            return {**super().status(session_id), **public_phase,
                    'manuals_valid': (phase.get('report', {}).get('valid', 0)
                                      if phase.get('report', {}).get('manuals_prepared') is not False else 0),
                    'manuals_prepared': phase.get('report', {}).get('manuals_prepared', True),
                    'refresh_max_misses': self.refresh_max_misses,
                    'configuration_directory': str(self.home)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    args = parser.parse_args(argv)
    if sys.version_info < (3, 12):
        parser.error('Python 3.12 or later is required')
    try:
        with WorkerBridge(args.directory) as bridge:
            # Private parent/child handshake. The parent never forwards this
            # line to host logs, UI, command arguments or a credential file.
            print(json.dumps({'protocol': 1, 'url': bridge.url, 'token': bridge.token}), flush=True)
            for _line in sys.stdin:
                pass
    except Exception as error:
        print(f'Toollery worker failed ({type(error).__name__}). Check local configuration.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
