"""Local Messages/SSE test double. Runs the REAL DSH loop and real demo tools.

Only model decisions are scripted. Never use this server to report retrieval
quality or real-model capability; fixture requests are deliberately explicit.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


class MockMessages:
    def __init__(self, choose_call=None):
        self.requests = []
        self.choose_call = choose_call

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                # Capture body only; never persist Authorization/x-api-key headers.
                owner.requests.append(body)
                # DSH may append system tool_addition/removal blocks after the
                # user message when tools change within this same session.
                content = next(message['content'] for message in reversed(body['messages']) if message['role'] == 'user')
                if isinstance(content, str):
                    content = [{'type': 'text', 'text': content}]
                results = [block for block in content if block['type'] == 'tool_result']
                if results:
                    answer = json.dumps(results, ensure_ascii=False)
                    call = None
                else:
                    prompt = '\n'.join(block.get('text', '') for block in content if block['type'] == 'text')
                    if owner.choose_call is not None:
                        call = owner.choose_call(prompt, body.get('tools', []))
                    elif '[guard_probe]' in prompt:
                        call = ('multiply_numbers', {'a': 7, 'b': 6})
                    elif 'add_numbers' in prompt:
                        call = ('add_numbers', {'a': 19, 'b': 23})
                    elif 'celsius_to_fahrenheit' in prompt:
                        call = ('celsius_to_fahrenheit', {'celsius': 20})
                    elif 'multiply_numbers' in prompt:
                        call = ('multiply_numbers', {'a': 6, 'b': 7})
                    elif 'sha256_text' in prompt:
                        call = ('sha256_text', {'text': 'hello'})
                    else:
                        call = None
                    answer = 'No selected tool is needed.'
                events = [{
                    'type': 'message_start',
                    'message': {'id': f'mock-{len(owner.requests)}', 'type': 'message', 'role': 'assistant',
                                'model': body.get('model', 'mock'), 'content': [],
                                'usage': {'input_tokens': 3, 'output_tokens': 0}},
                }]
                if call:
                    name, arguments = call
                    events.extend([
                        {'type': 'content_block_start', 'index': 0, 'content_block': {
                            'type': 'tool_use', 'id': f'call-{len(owner.requests)}', 'name': name, 'input': {}}},
                        {'type': 'content_block_delta', 'index': 0, 'delta': {
                            'type': 'input_json_delta', 'partial_json': json.dumps(arguments)}},
                    ])
                else:
                    events.extend([
                        {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
                        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': answer}},
                    ])
                events.extend([
                    {'type': 'content_block_stop', 'index': 0},
                    {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use' if call else 'end_turn', 'stop_sequence': None},
                     'usage': {'output_tokens': 10}},
                    {'type': 'message_stop'},
                ])
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                for event in events:
                    self.wfile.write(('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode())
                self.wfile.flush()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
