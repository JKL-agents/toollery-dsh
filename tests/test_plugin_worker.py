"""Plugin worker: explicit generation, cache updates and host eligibility."""
import copy
import json
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

from toollery.retrieval import BM25Index
from toollery.manuals import ManualCache
from toollery.generation import MockGenerator
from toollery.adapters.dsh import StaleRefresh
from toollery_dsh.worker import WorkerBridge, initialize
from toollery_dsh.onboarding import provider_view, manual_key


TOOLS = [
    {'name': 'sum', 'description': 'Add numbers and calculate their sum', 'parameters': {'type': 'object'}},
    {'name': 'temperature', 'description': 'Convert Celsius Fahrenheit temperature', 'parameters': {'type': 'object'}},
]


class PluginWorkerTests(unittest.TestCase):
    def test_progress_reports_elapsed_rate_and_approximate_eta_without_internal_clock(self):
        fields = WorkerBridge._progress_fields(2, 10, time.monotonic() - 4)
        self.assertGreaterEqual(fields['elapsed_seconds'], 3.5)
        self.assertAlmostEqual(fields['rate_per_second'], 0.5, delta=0.1)
        self.assertAlmostEqual(fields['eta_seconds'], 16, delta=1.0)
        self.assertIsNone(WorkerBridge._progress_fields(0, 10, time.monotonic())['eta_seconds'])
        state = {'completed': 10000, 'total': 10001, '_generation_completed': 0,
                 '_generation_total': 1, '_started_monotonic': time.monotonic() - 4}
        self.assertIsNone(WorkerBridge._timing(state)['eta_seconds'])

    def test_onboarding_configuration_is_redacted_no_generation_and_key_rotation_needs_no_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = WorkerBridge(directory)
            self.assertFalse(provider_view(directory)['configured'])
            settings = {'model': 'fixture', 'api_url': 'https://example.com/v1', 'api_key': 'first-secret'}
            result = bridge.generator_settings({'settings': settings})
            self.assertTrue(result['ok'])
            self.assertTrue(result['generator']['key_configured'])
            self.assertNotIn('first-secret', json.dumps(result))
            bridge.capture('native', TOOLS)
            bridge.jobs['native'].join(10)
            self.assertEqual(bridge.status('native')['phase'], 'needs_preparation')
            self.assertFalse(list((bridge.home / 'cache').rglob('*.json')))
            # Saving a new key changes only secret storage, not cache identity.
            before = (bridge.home / 'manual-generator.json').read_text()
            bridge.generator_settings({'settings': {**settings, 'api_key': 'second-secret'}})
            bridge.jobs['native'].join(10)
            self.assertEqual((bridge.home / 'manual-generator.json').read_text(), before)
            self.assertEqual(manual_key(directory, json.loads(before)), 'second-secret')
            retained = bridge.generator_settings({'settings': {**settings, 'api_key': ''}})
            bridge.jobs['native'].join(10)
            self.assertTrue(retained['generator']['configured'])
            # No secrets in locally constructed validation messages.
            bad = bridge.generator_settings({'settings': {**settings, 'api_url': 'https://secret@example.com'}})
            self.assertFalse(bad['ok'])
            self.assertNotIn('secret@example', json.dumps(bad))
            with patch.dict('os.environ', {'TOOLLERY_MANUAL_API_KEY': 'environment-old-key'}):
                self.assertEqual(manual_key(directory, json.loads(before)), 'second-secret')

    def test_eligibility_is_applied_before_top_k_limit(self):
        index = BM25Index({'denied': 'apple apple apple', 'allowed': 'apple pear', 'noise': 'banana'})
        self.assertEqual([h.tool_id for h in index.search('apple', 1, eligible_ids=['allowed'])], ['allowed'])
        self.assertEqual([h.tool_id for h in index.search('apple', None, eligible_ids=['allowed', 'noise'])], ['allowed', 'noise'])
        self.assertEqual(index.search('apple', 1, eligible_ids=[]), [])

    def test_skills_target_bypasses_tool_retrieval_and_keeps_host_eligible_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = WorkerBridge(directory)
            snapshot = bridge.capture('skills', TOOLS)
            bridge.jobs['skills'].join(10)
            bridge.configure('skills', enabled=True, retrieval_target='skills', k=1)
            status = bridge.status('skills')
            self.assertEqual(status['retrieval_target'], 'skills')
            selection = bridge.select({'session_id': 'skills', 'snapshot_hash': snapshot['snapshot_hash'],
                                       'pool_revision': status['pool_revision'], 'prompt': 'Add numbers',
                                       'eligible_names': ['sum', 'temperature']})
            self.assertEqual(selection['retrieval_target'], 'skills')
            self.assertEqual(selection['selected_names'], ['sum', 'temperature'])
            self.assertEqual(selection['retrieval_ms'], 0.0)

    def test_explicit_preparation_off_reuse_and_changed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            home = initialize(directory)
            (home / 'manual-generator.json').write_text(json.dumps({'provider': 'mock'}))
            bridge = WorkerBridge(home)
            snapshot = bridge.capture('first', TOOLS)
            bridge.jobs['first'].join(10)
            self.assertEqual(bridge.status('first')['phase'], 'needs_preparation')
            self.assertFalse(list((home / 'cache').rglob('*.json')))
            with self.assertRaises(ValueError):
                bridge.request_preparation({'session_id': 'first'})
            # An OFF choice must survive background completion.
            bridge.configure('first', enabled=False)
            bridge.request_preparation({'session_id': 'first', 'allow_generation': True})
            bridge.jobs['first'].join(10)
            status = bridge.status('first')
            self.assertEqual((status['phase'], status['manuals_valid'], status['enabled']), ('ready', 2, False))
            bridge.configure('first', enabled=True, representation='spec', k=1)
            payload = {'session_id': 'first', 'snapshot_hash': snapshot['snapshot_hash'],
                       'prompt': 'Add numbers', 'eligible_names': ['temperature']}
            self.assertEqual(bridge.select(payload)['selected_names'], [])
            bridge.configure('first', enabled=False)
            self.assertEqual(bridge.select(payload)['selected_names'], ['temperature'])

            # A second process/session sees an unchanged cache without generation.
            second = WorkerBridge(home)
            second.capture('second', TOOLS)
            second.jobs['second'].join(10)
            self.assertEqual(second.status('second')['report']['cache_hits'], 2)
            changed = copy.deepcopy(TOOLS)
            changed[0]['description'] += ' Supports negative values'
            second.capture('changed', changed)
            second.jobs['changed'].join(10)
            self.assertEqual(second.status('changed')['manuals_valid'], 1)
            second.request_preparation({'session_id': 'changed', 'allow_generation': True})
            second.jobs['changed'].join(10)
            self.assertEqual((second.status('changed')['report']['cache_hits'], second.status('changed')['report']['generated']), (1, 1))
            second.release({'session_id': 'changed'})
            self.assertNotIn('changed', second.sessions)

    def test_refresh_generates_only_new_manual_and_advances_pool_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            home = initialize(directory)
            (home / 'manual-generator.json').write_text(json.dumps({'provider': 'mock'}))
            bridge = WorkerBridge(home)
            initial = bridge.capture('native', TOOLS)
            bridge.jobs['native'].join(10)
            bridge.request_preparation({'session_id': 'native', 'allow_generation': True})
            bridge.jobs['native'].join(10)
            before = bridge.status('native')
            self.assertEqual(before['phase'], 'ready')
            expanded = TOOLS + [
                {'name': 'multiply', 'description': 'Multiply two numbers', 'parameters': {'type': 'object'}},
            ]
            accepted = bridge.request_refresh({
                'session_id': 'native', 'schemas': expanded,
                'snapshot_hash': initial['snapshot_hash'], 'pool_revision': before['pool_revision'],
                'max_auto_tools': 15,
            })
            self.assertEqual(accepted['status'], 'accepted')
            job = next(item for item in bridge.refresh_jobs.values() if item['job_id'] == accepted['job_id'])
            job['thread'].join(10)
            refreshed = bridge.refresh_status({'session_id': 'native', 'job_id': accepted['job_id']})
            self.assertEqual(refreshed['phase'], 'committed')
            self.assertEqual(refreshed['result']['diff']['added'], ['multiply'])
            status = bridge.status('native')
            self.assertEqual(status['tool_names'], ['multiply', 'sum', 'temperature'])
            self.assertEqual(status['pool_revision'], before['pool_revision'] + 1)
            self.assertEqual(status['report']['generated'], 1)
            self.assertIn('elapsed_seconds', status)
            self.assertIn('eta_seconds', status)
            self.assertNotIn('_started_monotonic', status)
            bridge.configure('native', k=1)
            status = bridge.status('native')
            selection = bridge.select({
                'session_id': 'native', 'snapshot_hash': status['snapshot_hash'],
                'pool_revision': status['pool_revision'], 'prompt': 'Multiply numbers',
                'eligible_names': status['tool_names'],
            })
            self.assertEqual(selection['selected_names'], ['multiply'])

    def test_refresh_rejects_old_base_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            home = initialize(directory)
            (home / 'manual-generator.json').write_text(json.dumps({'provider': 'mock'}))
            bridge = WorkerBridge(home)
            snapshot = bridge.capture('native', TOOLS)
            bridge.jobs['native'].join(10)
            with self.assertRaises(StaleRefresh):
                bridge.refresh_pool('native', TOOLS + [{'name': 'new', 'description': 'New tool', 'parameters': {}}],
                                    bridge._configured_generator(generate=False),
                                    ManualCache(bridge.home / 'cache'),
                                    expected_pool_revision=1, expected_snapshot_hash=snapshot['snapshot_hash'])

    def test_refresh_approval_targets_new_pool_and_waiting_retry_reuses_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = initialize(directory)
            (home / 'manual-generator.json').write_text(json.dumps({'provider': 'mock'}))
            bridge = WorkerBridge(home)
            bridge.capture('native', TOOLS)
            bridge.jobs['native'].join(10)
            bridge.request_preparation({'session_id': 'native', 'allow_generation': True})
            bridge.jobs['native'].join(10)
            before = bridge.status('native')
            request = {'session_id': 'native', 'schemas': TOOLS + [
                {'name': 'new', 'description': 'A new capability', 'parameters': {}}],
                'snapshot_hash': before['snapshot_hash'], 'pool_revision': before['pool_revision'],
                'max_auto_tools': 0}
            self.assertEqual(bridge.request_refresh(request)['status'], 'approval_required')
            self.assertEqual(bridge.status('native')['pending_tool_count'], 3)
            reverted = bridge.request_refresh({**request, 'schemas': TOOLS})
            self.assertEqual(reverted['status'], 'unchanged')
            self.assertEqual(bridge.status('native')['phase'], 'ready')
            self.assertIsNone(bridge.status('native')['pending_tool_count'])
            self.assertNotIn('native', bridge.pending_refreshes)
            self.assertEqual(bridge.request_refresh(request)['status'], 'approval_required')
            entered, finish = threading.Event(), threading.Event()
            generator = MockGenerator()
            original = generator.generate
            calls = []
            def generate(*args):
                calls.append(1)
                entered.set()
                if not finish.wait(5):
                    raise RuntimeError('Fixture generation gate expired')
                return original(*args)
            generator.generate = generate
            with patch.object(bridge, '_configured_generator', return_value=generator):
                accepted = bridge.request_preparation({'session_id': 'native', 'allow_generation': True})
                job = bridge.refresh_jobs[('native', accepted['snapshot_hash'])]
                try:
                    self.assertTrue(entered.wait(5))
                    first = bridge.refresh_status({'session_id': 'native', 'job_id': accepted['job_id']})
                    time.sleep(0.02)
                    waiting = bridge.refresh_status({'session_id': 'native', 'job_id': accepted['job_id']})
                    self.assertGreater(waiting['elapsed_seconds'], first['elapsed_seconds'])
                    self.assertIsNone(waiting['eta_seconds'])
                    retry = bridge.request_preparation({'session_id': 'native', 'allow_generation': True})
                    self.assertEqual(retry['job_id'], accepted['job_id'])
                    self.assertEqual(len(calls), 1)
                finally:
                    finish.set()
                    job['thread'].join(10)
            after = bridge.status('native')
            self.assertEqual((after['phase'], after['tool_count'], after['manuals_valid']), ('ready', 3, 3))
            self.assertNotIn('native', bridge.pending_refreshes)
            self.assertEqual(bridge.status('native')['elapsed_seconds'], after['elapsed_seconds'])
            # Report the mode frozen for this selection, despite changed UI settings.
            selection = bridge.select({'session_id': 'native', 'prompt': 'new capability',
                'snapshot_hash': after['snapshot_hash'], 'options': {'representation': 'spec'}})
            self.assertEqual(selection['representation'], 'spec')

    def test_releasing_session_waits_for_active_refresh_then_cleans_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            home = initialize(directory)
            (home / 'manual-generator.json').write_text(json.dumps({'provider': 'mock'}))
            bridge = WorkerBridge(home)
            initial = bridge.capture('native', TOOLS)
            bridge.jobs['native'].join(10)
            entered, finish = threading.Event(), threading.Event()
            generator = MockGenerator()
            original = generator.generate
            def generate(*args):
                entered.set()
                if not finish.wait(5):
                    raise RuntimeError('Fixture generation gate expired')
                return original(*args)
            generator.generate = generate
            with patch.object(bridge, '_configured_generator', return_value=generator):
                accepted = bridge.request_refresh({'session_id': 'native', 'schemas': TOOLS + [
                    {'name': 'new', 'description': 'New capability', 'parameters': {}}],
                    'snapshot_hash': initial['snapshot_hash']})
                job = bridge.refresh_jobs[('native', accepted['snapshot_hash'])]
                try:
                    self.assertTrue(entered.wait(5))
                    bridge.release({'session_id': 'native'})
                    self.assertIn('native', bridge.sessions)
                finally:
                    finish.set()
                    job['thread'].join(10)
            self.assertNotIn('native', bridge.sessions)
            self.assertFalse(bridge.refresh_jobs)
            self.assertFalse(bridge.pending_refreshes)

    def test_discovery_ranks_provider_batch_without_mutating_host_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            home = initialize(directory)
            (home / 'manual-generator.json').write_text(json.dumps({'provider': 'mock'}))
            bridge = WorkerBridge(home)
            snapshot = bridge.capture('native', TOOLS)
            bridge.jobs['native'].join(10)
            before = bridge.status('native')
            candidates = [
                {'schema': {'name': 'calendar.create_event', 'description': 'Create calendar events', 'parameters': {'type': 'object'}},
                 'tool_id': 'calendar.create_event', 'candidate_ref': 'provider-ref-17',
                 'provider_id': 'calendar-provider', 'executor': {'opaque': 'provider-only'}, 'source': 'remote'},
                {'name': 'weather', 'description': 'Get weather forecasts', 'parameters': {'type': 'object'}},
            ]
            accepted = bridge.request_discovery({'session_id': 'native', 'prompt': 'Create an event',
                                                'candidates': candidates, 'representation': 'spec_manual',
                                                'budget': 'top_k', 'k': 1, 'request_id': 'discovery-request-1'})
            self.assertEqual(accepted['status'], 'accepted')
            job = bridge.discovery_status({'session_id': 'native', 'job_id': accepted['job_id']})
            bridge.discovery_jobs[next(key for key, value in bridge.discovery_jobs.items()
                                      if value['job_id'] == accepted['job_id'])]['thread'].join(10)
            result = bridge.discovery_status({'session_id': 'native', 'job_id': accepted['job_id']})['result']
            self.assertEqual(result['status'], 'ranked')
            self.assertEqual(result['candidate_count'], 2)
            self.assertEqual(result['request_id'], 'discovery-request-1')
            self.assertRegex(result['candidate_snapshot_hash'], r'^sha256:')
            self.assertEqual(result['selection']['method'], 'spec_manual')
            self.assertEqual(result['selected'][0]['tool_id'], 'calendar.create_event')
            self.assertEqual(result['selected'][0]['candidate_ref'], 'provider-ref-17')
            self.assertRegex(result['selected'][0]['spec_hash'], r'^sha256:')
            self.assertEqual(result['selected_names'], ['calendar.create_event'])
            self.assertEqual(result['manual_report']['generated'], 2)
            self.assertEqual(bridge.status('native')['snapshot_hash'], snapshot['snapshot_hash'])
            self.assertEqual(bridge.status('native')['tool_count'], before['tool_count'])
            catalog = json.loads((home / 'discovery-catalog.json').read_text())
            self.assertEqual(sorted(catalog), ['calendar.create_event', 'weather'])
            self.assertNotIn('executor', json.dumps(catalog))
            self.assertNotIn('source', json.dumps(catalog))
            retry = bridge.request_discovery({'session_id': 'native', 'prompt': 'Create an event',
                                              'candidates': candidates, 'representation': 'spec_manual',
                                              'budget': 'top_k', 'k': 1, 'request_id': 'discovery-request-2'})
            self.assertEqual(retry['status'], 'ranked')
            self.assertEqual(retry['request_id'], 'discovery-request-1')
            self.assertEqual(retry['selected_names'], ['calendar.create_event'])

    def test_discovery_requires_approval_for_large_missing_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            home = initialize(directory)
            (home / 'manual-generator.json').write_text(json.dumps({'provider': 'mock'}))
            bridge = WorkerBridge(home)
            bridge.capture('native', TOOLS)
            bridge.jobs['native'].join(10)
            candidates = [{'name': f'tool_{index}', 'description': 'A distinct capability',
                           'parameters': {'type': 'object'}} for index in range(3)]
            accepted = bridge.request_discovery({'session_id': 'native', 'prompt': 'capability',
                                                'candidates': candidates, 'max_auto_tools': 1})
            bridge.discovery_jobs[next(key for key, value in bridge.discovery_jobs.items()
                                      if value['job_id'] == accepted['job_id'])]['thread'].join(10)
            result = bridge.discovery_status({'session_id': 'native', 'job_id': accepted['job_id']})['result']
            self.assertEqual(result['status'], 'approval_required')
            self.assertEqual(result['missing'], 3)

    def test_skill_metadata_search_does_not_load_bodies_or_generate_manuals(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = WorkerBridge(directory)
            result = bridge.request_skill_search({'prompt': 'render a spreadsheet chart', 'budget': 'top_k', 'k': 1,
                'candidates': [
                    {'name': 'office-docs', 'description': 'Create and edit office documents'},
                    {'name': 'office-xlsx', 'description': 'Read and render spreadsheets and charts',
                     'when_to_use': 'Use for workbook formulas and chart output', 'tags': ['spreadsheet', 'chart']},
                ]})
            self.assertEqual(result['status'], 'ranked')
            self.assertEqual(result['selected_names'], ['office-xlsx'])
            self.assertEqual(result['selection']['method'], 'metadata_bm25')
            self.assertFalse((bridge.home / 'discovery-catalog.json').exists())
            self.assertFalse(list((bridge.home / 'cache').rglob('*.json')))

    def test_skill_search_prioritizes_explicit_name_and_name_terms(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = WorkerBridge(directory)
            result = bridge.request_skill_search({'prompt': 'Use sample-agent-architecture to design an AI assistant architecture',
                'budget': 'top_k', 'k': 3, 'candidates': [
                    {'name': 'sample-agent-architecture', 'description': 'Design AI agent architectures'},
                    {'name': 'sample-agent-governance', 'description': 'Governance controls for AI agent systems'},
                    {'name': 'sample-technical-job-search', 'description': 'Job search for AI software engineers'},
                    {'name': 'sample-doc-and-modernize', 'description': 'Document and modernize a codebase'},
                ]})
            self.assertEqual(result['selected_names'][0], 'sample-agent-architecture')
            self.assertEqual(len(result['selected_names']), 3)
            self.assertEqual(result['selection']['method'], 'metadata_bm25_exact_name')

    def test_skill_exact_matches_fill_slots_without_exceeding_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = WorkerBridge(directory)
            candidates = [{'name': f'skill-{i:02}', 'description': 'meeting notes and action items'}
                          for i in range(25)]
            for k in (1, 3, 10, 20):
                with self.subTest(k=k):
                    result = bridge.request_skill_search({'prompt': 'Use skill-00 for meeting notes',
                        'budget': 'top_k', 'k': k, 'candidates': candidates})
                    self.assertEqual(len(result['selected_names']), k)
                    self.assertEqual(len(set(result['selected_names'])), k)
                    self.assertEqual(result['selected_names'][0], 'skill-00')
                    json.dumps(result, allow_nan=False)
            for budget, expected in [('top_k', 2), ('all', 25)]:
                with self.subTest(budget=budget):
                    result = bridge.request_skill_search({'prompt': 'Use skill-00, skill-01 and skill-02',
                        'budget': budget, 'k': 2, 'candidates': candidates})
                    self.assertEqual(len(result['selected_names']), expected)
                    self.assertEqual(len(set(result['selected_names'])), expected)
            boundary = bridge.request_skill_search({'prompt': 'Use skill-00-extra for meeting notes',
                'k': 3, 'candidates': candidates})
            self.assertEqual(boundary['selection']['method'], 'metadata_bm25')
            no_hits = bridge.request_skill_search({'prompt': 'unmatchedword', 'k': 3, 'candidates': candidates})
            self.assertEqual(no_hits['selected_names'], [])

    def test_skill_search_accepts_empty_catalog_and_native_blank_description(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = WorkerBridge(directory)
            empty = bridge.request_skill_search({'prompt': 'find instructions', 'candidates': []})
            self.assertEqual(empty['candidate_count'], 0)
            self.assertEqual(empty['selected_names'], [])
            named = bridge.request_skill_search({'prompt': 'meeting minutes', 'candidates': [
                {'name': 'meeting-minutes', 'description': ''}]})
            self.assertEqual(named['selected_names'], ['meeting-minutes'])
            self.assertFalse(list((bridge.home / 'cache').rglob('*.json')))


if __name__ == '__main__':
    unittest.main()
