"""Local recording endpoint for real DSH provider wire projection.

Offline replies are protocol fixtures and never quality measurements. Paid mode
relays the unchanged body to DeepSeek; keys/headers are not written to disk.
"""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from uuid import uuid4
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from toollery.storage import canonical, digest


class RecordingMessages:
    def __init__(self, output: Path, *, key=None, paid_request_limit=None):
        self.output, self.key = output, key
        if paid_request_limit is not None and paid_request_limit < 1:
            raise ValueError('paid_request_limit must be positive')
        self.paid_request_limit = paid_request_limit
        self.paid_request_count = 0
        self.paid = False
        self.lock = threading.Lock()
        self.count = 0
        self.transport_id = uuid4().hex

    def __enter__(self):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_args):
                pass

            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, ConnectionAbortedError):
                    # DSH may close an idle keep-alive socket during shutdown.
                    pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                body = json.loads(raw)
                tools = body.get('tools', [])
                record = {'transport_id': owner.transport_id, 'body_hash': digest(body), 'tools_hash': digest(tools),
                          'tool_names': [tool['name'] for tool in tools],
                          'schema_utf8_bytes': len(canonical(tools).encode()),
                          'request_utf8_bytes': len(raw), 'message_count': len(body['messages']),
                          'deferred_tools': sum(bool(tool.get('defer_loading')) for tool in tools)}
                with owner.lock:
                    owner.count += 1
                    record['wire_index'] = owner.count
                    with (owner.output / 'wire-audit.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(canonical({**record, 'paid': owner.paid}) + '\n')
                if owner.paid:
                    # A preflight can cap remote calls even if a provider retries.
                    with owner.lock:
                        allowed = (owner.paid_request_limit is None
                                   or owner.paid_request_count < owner.paid_request_limit)
                        if allowed:
                            owner.paid_request_count += 1
                    if not allowed:
                        payload = canonical({'error': {'type': 'preflight_request_limit',
                            'message': 'Remote request budget exhausted; no additional API call sent.'}}).encode()
                        self.send_response(429)
                        self.send_header('Content-Length', str(len(payload)))
                        self.end_headers(); self.wfile.write(payload)
                        return
                    # DSH already performed provider-specific projection; relay bytes untouched.
                    started = time.perf_counter()
                    request = Request('https://api.deepseek.com/anthropic/v1/messages', data=raw,
                                      headers={'Content-Type': 'application/json', 'x-api-key': owner.key,
                                               'anthropic-version': '2023-06-01'})
                    try:
                        response = urlopen(request, timeout=180)
                    except HTTPError as error:
                        payload = error.read()
                        with owner.lock:
                            with (owner.output / 'provider-errors.jsonl').open('a', encoding='utf-8') as stream:
                                stream.write(canonical({'status': error.code, 'wire_index': record['wire_index'],
                                                        'relay_ms': (time.perf_counter()-started)*1000,
                                                        'body': payload.decode('utf-8', 'replace')}) + '\n')
                        self.send_response(error.code)
                        self.send_header('Content-Length', str(len(payload)))
                        self.end_headers(); self.wfile.write(payload)
                        return
                    except Exception as error:
                        with owner.lock:
                            with (owner.output / 'provider-errors.jsonl').open('a', encoding='utf-8') as stream:
                                stream.write(canonical({'status': None, 'wire_index': record['wire_index'],
                                    'relay_ms': (time.perf_counter()-started)*1000,
                                    'error_type': type(error).__name__}) + '\n')
                        payload = canonical({'error': {'type': 'relay_error', 'message': type(error).__name__}}).encode()
                        self.send_response(502); self.send_header('Content-Length', str(len(payload)))
                        self.end_headers(); self.wfile.write(payload)
                        return
                    with response:
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        usage, model, first_delta_ms = {}, None, None
                        try:
                            while chunk := response.readline():
                                if chunk.startswith(b'data: '):
                                    try:
                                        event = json.loads(chunk[6:])
                                        if event.get('type') == 'message_start':
                                            usage.update(event['message'].get('usage', {}))
                                            model = event['message'].get('model')
                                        if event.get('type') == 'message_delta':
                                            usage.update(event.get('usage', {}))
                                        if event.get('type') == 'content_block_delta' and first_delta_ms is None:
                                            first_delta_ms = (time.perf_counter() - started) * 1000
                                    except ValueError:
                                        pass
                                self.wfile.write(chunk); self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            pass
                        self.close_connection = True
                        with owner.lock:
                            with (owner.output / 'provider-usage.jsonl').open('a', encoding='utf-8') as stream:
                                stream.write(canonical({'transport_id': owner.transport_id, 'wire_index': record['wire_index'], 'body_hash': record['body_hash'],
                                    'model': model, 'usage': usage, 'relay_ttft_ms': first_delta_ms,
                                    'relay_ms': (time.perf_counter()-started)*1000}) + '\n')
                    return
                answer = canonical(record)
                events = [
                    {'type': 'message_start', 'message': {'id': 'projection-fixture', 'type': 'message',
                        'role': 'assistant', 'model': body['model'], 'content': [],
                        'usage': {'input_tokens': 0, 'output_tokens': 0}}},
                    {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
                    {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': answer}},
                    {'type': 'content_block_stop', 'index': 0},
                    {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 0}},
                    {'type': 'message_stop'},
                ]
                data = ''.join('event: ' + e['type'] + '\ndata: ' + canonical(e) + '\n\n' for e in events).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        return self

    def __exit__(self, *_args):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)
