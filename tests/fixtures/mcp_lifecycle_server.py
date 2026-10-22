"""Small real stdio MCP server with a startup-only, file-backed tool inventory.

This is a controlled protocol fixture, not a model or discovery mock. Each
process loads the same manifest path anew; stdout is reserved for JSON-RPC.
"""
import json
import os
from pathlib import Path
import sys

manifest = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
log = Path(sys.argv[2])
tools = {tool['name']: tool for tool in manifest['tools']}


def record(method, **extra):
    with log.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'pid': os.getpid(), 'stage': manifest['stage'],
                                 'method': method, **extra}) + '\n')


record('startup', names=sorted(tools))
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    record(method)
    if 'id' not in request:
        continue
    error = None
    if method == 'initialize':
        result = {'protocolVersion': request['params']['protocolVersion'],
                  'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'toollery-lifecycle-fixture', 'version': '1.0.0'}}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': list(tools.values())}
    elif method in ('resources/list', 'resources/templates/list'):
        result = {'resources' if method == 'resources/list' else 'resourceTemplates': []}
    elif method == 'tools/call':
        params = request['params']
        tool = tools.get(params['name'])
        arguments = params.get('arguments', {})
        if tool is None:
            result = {'isError': True, 'content': [{'type': 'text', 'text': 'Unknown tool'}]}
        elif any(not isinstance(arguments.get(key), str) for key in tool['inputSchema']['required']):
            result = {'isError': True, 'content': [{'type': 'text', 'text': 'Invalid required arguments'}]}
        else:
            key = tool['inputSchema']['required'][0]
            text = params['name'] + ':' + arguments[key]
            record('executed', name=params['name'], arguments=arguments, result=text)
            result = {'content': [{'type': 'text', 'text': text}]}
    else:
        error = {'code': -32601, 'message': 'Method not found'}
    response = {'jsonrpc': '2.0', 'id': request['id']}
    response['error' if error else 'result'] = error or result
    print(json.dumps(response), flush=True)
