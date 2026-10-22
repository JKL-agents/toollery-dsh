from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from toollery.adapters.dsh import DSHBridge, snapshot_pool
from toollery.generation import MockGenerator
from toollery.manuals import ManualCache, PreparationError


SCHEMAS = [
    {'name': 'add', 'description': 'Add numbers and return their sum.',
     'parameters': {'type': 'object', 'properties': {'a': {'type': 'number'}}, 'required': ['a']}},
    {'name': 'temperature', 'description': 'Convert Celsius to Fahrenheit.',
     'parameters': {'type': 'object', 'properties': {'celsius': {'type': 'number'}}}},
]


class DSHTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bridge = DSHBridge(self.root / 'run', k=1)
        self.snapshot = self.bridge.capture('agent-a', SCHEMAS)
        self.cache = ManualCache(self.root / 'cache')

    def payload(self, text='Add numbers', session='agent-a', snapshot=None):
        return {'session_id': session, 'snapshot_hash': (snapshot or self.snapshot)['snapshot_hash'], 'prompt': text, 'turn': 1}

    def test_frozen_snapshot_detaches_original_and_rejects_changes(self):
        schemas = copy.deepcopy(SCHEMAS)
        pool = snapshot_pool(schemas)
        schemas[0]['parameters']['required'].append('mutated')
        self.assertEqual(pool.tools['add'].documentation, SCHEMAS[0])
        self.assertEqual(self.bridge.capture('agent-a', list(reversed(SCHEMAS))), self.snapshot)
        with self.assertRaises(ValueError):
            self.bridge.capture('agent-a', schemas)
        for invalid in ([], [SCHEMAS[0], SCHEMAS[0]], [{'name': 'bad'}]):
            with self.assertRaises(ValueError):
                snapshot_pool(invalid)

    def test_missing_or_corrupt_manual_blocks_ready_without_spec_fallback(self):
        with self.assertRaises(RuntimeError):
            self.bridge.select(self.payload())
        with self.assertRaises(PreparationError):
            self.bridge.prepare('agent-a', MockGenerator(), self.cache, cache_only=True)
        self.assertFalse(self.bridge.sessions['agent-a'].ready)
        first = self.bridge.prepare('agent-a', MockGenerator(), self.cache)
        self.assertEqual(first['generated'], 2)
        self.assertEqual(first['proxy_query_count'], 3)
        next(self.cache.root.rglob('*.json')).write_text('{}', encoding='utf-8')
        with self.assertRaises(PreparationError):
            self.bridge.prepare('agent-a', MockGenerator(), self.cache, cache_only=True)
        with self.assertRaises(RuntimeError):
            self.bridge.select(self.payload())

    def test_modes_and_next_prompt_selection_keep_original_specs(self):
        self.bridge.prepare('agent-a', MockGenerator(), self.cache)
        original = copy.deepcopy(self.bridge.sessions['agent-a'].pool.tools['add'].documentation)
        for mode in ('spec', 'manual', 'spec_manual'):
            self.bridge.configure('agent-a', representation=mode, budget='top_k', k=1)
            selected = self.bridge.select(self.payload('Add numbers'))
            self.assertEqual(selected['selected_names'], ['add'])
            self.assertEqual(self.bridge.select(self.payload('Celsius Fahrenheit'))['selected_names'], ['temperature'])
            self.bridge.configure('agent-a', budget='all')
            self.assertEqual(set(self.bridge.select(self.payload('zzzxqvv9001'))['selected_names']), {'add', 'temperature'})
        self.assertEqual(self.bridge.sessions['agent-a'].pool.tools['add'].documentation, original)
        self.bridge.configure('agent-a', budget='top_k')
        self.assertEqual(self.bridge.select(self.payload('zzzxqvv9001'))['selected_names'], [])

    def test_cache_reuse_and_changed_tool_regenerates_only_changed_entry(self):
        self.bridge.prepare('agent-a', MockGenerator(), self.cache)
        second = self.bridge.prepare('agent-a', MockGenerator(), self.cache, cache_only=True)
        self.assertEqual((second['cache_hits'], second['generation_calls']), (2, 0))
        changed = copy.deepcopy(SCHEMAS)
        changed[0]['description'] += ' Supports negative values.'
        self.bridge.capture('agent-b', changed)
        third = self.bridge.prepare('agent-b', MockGenerator(), self.cache)
        self.assertEqual((third['cache_hits'], third['generated']), (1, 1))

    def test_agent_scope_and_stale_snapshot_are_enforced(self):
        other = self.bridge.capture('agent-b', [SCHEMAS[1]])
        for session in ('agent-a', 'agent-b'):
            self.bridge.prepare(session, MockGenerator(), self.cache)
        with self.assertRaises(ValueError):
            self.bridge.select(self.payload(session='agent-b'))
        result = self.bridge.select(self.payload('Celsius', session='agent-b', snapshot=other))
        self.assertEqual(result['full_pool_count'], 1)
        self.assertEqual(result['selected_names'], ['temperature'])
        self.assertEqual(self.bridge.sessions['agent-a'].k, 1)

    def test_explicit_spec_only_never_creates_manuals(self):
        self.bridge.configure('agent-a', representation='spec')
        self.bridge.prepare('agent-a', MockGenerator(), self.cache, with_manuals=False)
        self.assertEqual(self.bridge.select(self.payload())['selected_names'], ['add'])
        self.assertFalse(self.cache.root.exists())
        with self.assertRaises(ValueError):
            self.bridge.configure('agent-a', representation='manual')

    def test_http_authentication_and_readiness(self):
        with self.bridge:
            def post(token):
                request = Request(self.bridge.url + '/select', data=json.dumps(self.payload()).encode(),
                                  headers={'Authorization': 'Bearer ' + token}, method='POST')
                return urlopen(request, timeout=5)
            with self.assertRaises(HTTPError) as unauthorized:
                post('wrong')
            self.assertEqual(unauthorized.exception.code, 401)
            with self.assertRaises(HTTPError) as unavailable:
                post(self.bridge.token)
            self.assertEqual(unavailable.exception.code, 503)
            self.bridge.prepare('agent-a', MockGenerator(), self.cache)
            with post(self.bridge.token) as response:
                self.assertEqual(json.load(response)['selected_names'], ['add'])

    def test_web_snapshot_prepares_before_reply_and_controls_are_session_scoped(self):
        def prepare(bridge, session_id):
            bridge.prepare(session_id, MockGenerator(), self.cache)
        self.bridge.on_snapshot = prepare
        with self.bridge:
            def post(path, payload, token=None):
                request = Request(self.bridge.url + path, data=json.dumps(payload).encode(),
                                  headers={'Authorization': 'Bearer ' + (token or self.bridge.token)}, method='POST')
                with urlopen(request, timeout=5) as response:
                    return json.load(response)
            for session in ('web-a', 'web-b'):
                post('/snapshot', {'session_id': session, 'schemas': SCHEMAS})
                self.assertTrue(post('/status', {'session_id': session})['ready'])
            status = post('/configure', {'session_id': 'web-a', 'options': {'k': 2, 'representation': 'manual'}})
            self.assertEqual((status['k'], status['manuals_valid']), (2, 2))
            self.assertEqual(post('/status', {'session_id': 'web-b'})['k'], 1)
            for endpoint, payload in (('/status', {'session_id': 'web-a'}),
                                      ('/configure', {'session_id': 'web-a', 'options': {'k': 1}})):
                with self.assertRaises(HTTPError) as denied:
                    post(endpoint, payload, 'wrong')
                self.assertEqual(denied.exception.code, 401)
            with self.assertRaises(HTTPError) as invalid:
                post('/configure', {'session_id': 'web-a', 'options': {'k': 0}})
            self.assertEqual(invalid.exception.code, 409)
            self.assertEqual(post('/status', {'session_id': 'web-a'})['k'], 2)
            post('/select', self.payload(session='web-a'))
            self.assertEqual(post('/status', {'session_id': 'web-a'})['last_selection']['selected_names'], ['add'])
            self.assertIsNone(post('/status', {'session_id': 'web-b'})['last_selection'])

    def test_web_missing_cache_blocks_snapshot_readiness(self):
        self.bridge.on_snapshot = lambda bridge, session: bridge.prepare(session, MockGenerator(), self.cache, cache_only=True)
        with self.bridge:
            request = Request(self.bridge.url + '/snapshot', data=json.dumps({'session_id': 'web', 'schemas': SCHEMAS}).encode(),
                              headers={'Authorization': 'Bearer ' + self.bridge.token}, method='POST')
            with self.assertRaises(HTTPError):
                urlopen(request, timeout=5)
            self.assertFalse(self.bridge.status('web')['ready'])

    def test_off_bypasses_retrieval_preserves_settings_and_session_isolation(self):
        self.bridge.prepare('agent-a', MockGenerator(), self.cache)
        self.bridge.capture('agent-b', SCHEMAS)
        first = self.bridge.select(self.payload())
        self.bridge.configure('agent-a', enabled=False)
        self.assertTrue(self.bridge.status('agent-b')['enabled'])
        # A selection already handed to DSH stays frozen for that turn.
        self.assertEqual(first['selected_names'], ['add'])
        self.assertTrue(first['enabled'])
        with patch.object(self.bridge.sessions['agent-a'].searches['spec_manual'], 'select',
                          side_effect=AssertionError('OFF must not retrieve')):
            full = self.bridge.select(self.payload('no matching terms'))
        self.assertFalse(full['enabled'])
        self.assertEqual(full['selected_names'], ['add', 'temperature'])
        self.assertEqual((full['ranked'], full['retrieval_ms']), ([], 0.0))
        with self.assertRaises(ValueError):
            self.bridge.select(self.payload(snapshot={'snapshot_hash': 'stale'}))
        self.bridge.configure('agent-a', enabled=True)
        restored = self.bridge.select(self.payload())
        self.assertEqual((restored['representation'], restored['budget'], restored['k']), ('spec_manual', 'top_k', 1))
        self.assertEqual(restored['selected_names'], ['add'])
        self.assertEqual(self.bridge.status('agent-a')['manuals_valid'], 2)

    def test_turn_cards_keep_selection_calls_and_configuration_across_restart(self):
        state_dir = self.root / 'ui-state'
        bridge = DSHBridge(self.root / 'first', enabled=False, state_dir=state_dir)
        snap = bridge.capture('web', SCHEMAS)
        selected = bridge.select(self.payload(session='web', snapshot=snap))
        call = {'session_id': 'web', 'selection_id': selected['selection_id'],
                'call_id': 'c1', 'name': 'add', 'status': 'running'}
        bridge.record_call(call)
        bridge.record_call({**call, 'status': 'success'})
        bridge.record_call({**call, 'call_id': 'c2', 'status': 'error'})
        bridge.configure('web', representation='manual', k=2)
        bridge.capture('other', SCHEMAS)
        self.assertEqual(bridge.status('other')['turns'], [])
        restored = DSHBridge(self.root / 'second', state_dir=state_dir)
        restored.capture('web', SCHEMAS)
        status = restored.status('web')
        self.assertEqual((status['enabled'], status['representation'], status['k']), (False, 'manual', 2))
        turn = status['turns'][0]
        self.assertEqual(turn['representation'], 'spec_manual')
        self.assertEqual(len(turn['tool_details']), 2)
        self.assertEqual([c['status'] for c in turn['calls']], ['success', 'error'])
        with self.assertRaises(ValueError):
            restored.record_call({**call, 'name': 'not_selected'})

    def test_off_startup_skips_preparation_and_on_prepares_or_preserves_off_on_failure(self):
        for cache_only in (True, False):
            with self.subTest(cache_only=cache_only):
                calls = []
                def prepare(bridge, session):
                    calls.append(session)
                    bridge.prepare(session, MockGenerator(), self.cache, cache_only=cache_only)
                with DSHBridge(self.root / str(cache_only), enabled=False, on_snapshot=prepare, k=1) as bridge:
                    def post(path, payload):
                        request = Request(bridge.url + path, data=json.dumps(payload).encode(),
                                          headers={'Authorization': 'Bearer ' + bridge.token}, method='POST')
                        with urlopen(request, timeout=5) as response:
                            return json.load(response)
                    snap = post('/snapshot', {'session_id': 'web-off', 'schemas': SCHEMAS})
                    self.assertEqual(calls, [])
                    status = post('/status', {'session_id': 'web-off'})
                    self.assertTrue(status['ready'])
                    self.assertFalse(status['retrieval_ready'])
                    full = post('/select', self.payload(session='web-off', snapshot=snap))
                    self.assertEqual(full['selected_names'], ['add', 'temperature'])
                    with self.assertRaises(HTTPError):
                        post('/configure', {'session_id': 'web-off', 'options': {'enabled': 'true'}})
                    self.assertEqual(calls, [])
                    if cache_only:
                        with self.assertRaises(HTTPError):
                            post('/configure', {'session_id': 'web-off', 'options': {'enabled': True}})
                        self.assertFalse(bridge.status('web-off')['enabled'])
                        self.assertEqual(post('/select', self.payload(session='web-off', snapshot=snap))['selected_names'], full['selected_names'])
                    else:
                        status = post('/configure', {'session_id': 'web-off', 'options': {'enabled': True}})
                        self.assertTrue(status['enabled'])
                        self.assertTrue(status['retrieval_ready'])
                        self.assertEqual(post('/select', self.payload(session='web-off', snapshot=snap))['selected_names'], ['add'])
                    self.assertEqual(calls, ['web-off'])


if __name__ == '__main__':
    unittest.main()
