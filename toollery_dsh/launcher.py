"""Local browser setup for the Windows bootstrap and existing installations."""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import uuid
import webbrowser
from urllib.parse import urlsplit

from toollery.storage import atomic_json
from .configuration import ASSETS, ConfigurationError, initialize, load_config
from .runtime import runtime_entry

DEFAULTS = dict(pool='demo', custom_config='', port=3080, enabled=True,
                mode='spec_manual', budget='top_k', k=3, offline=False,
                generate=False, chat_model='deepseek-flash',
                chat_url='https://api.deepseek.com/anthropic',
                manual_model='deepseek-flash', manual_url='https://api.deepseek.com')
KEYS = ('TOOLLERY_CHAT_API_KEY', 'TOOLLERY_MANUAL_API_KEY')


def read_keys(home):
    """Read our own credential file without mutating the launcher's environment."""
    result = {}
    path = Path(home) / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            name, separator, value = line.partition('=')
            if separator and name in KEYS:
                result[name] = value
    return {name: result.get(name) or os.environ.get(name) or os.environ.get('TOOLLERY_API_KEY', '') for name in KEYS}


def validate_settings(body):
    if not isinstance(body, dict):
        raise ConfigurationError('Settings must be an object.')
    settings = {name: body.get(name, default) for name, default in DEFAULTS.items()}
    if settings['pool'] not in ('demo', 'filesystem', 'custom'):
        raise ConfigurationError('Select a supported tool pool.')
    if settings['mode'] not in ('spec', 'manual', 'spec_manual') or settings['budget'] not in ('all', 'top_k'):
        raise ConfigurationError('Select a supported retrieval mode and budget.')
    for name, low, high in [('port', 1, 65535), ('k', 1, 100000)]:
        value = settings[name]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ConfigurationError(f'{name} must be an integer in {low}..{high}.')
    for name in ('enabled', 'offline', 'generate'):
        if not isinstance(settings[name], bool):
            raise ConfigurationError(f'{name} must be true or false.')
    if settings['offline'] and settings['pool'] != 'demo':
        raise ConfigurationError('Scripted preview supports only the bundled utility pool.')
    if not isinstance(settings['custom_config'], str):
        raise ConfigurationError('Custom config must be a path.')
    for name in ('chat_model', 'manual_model'):
        value = settings[name]
        if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(c in value for c in '\r\n\0'):
            raise ConfigurationError('Model names must be nonempty single-line strings.')
    for name in ('chat_url', 'manual_url'):
        try:
            parts = urlsplit(settings[name])
            valid = (parts.scheme in {'https', 'http'} and parts.hostname and not parts.username
                     and not parts.password and not parts.query and not parts.fragment)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ConfigurationError('API URLs must be HTTP(S), without embedded credentials, query or fragment.')
    for name in ('chat_key', 'manual_key'):
        value = body.get(name, '')
        if not isinstance(value, str) or len(value) > 8192 or any(c in value for c in '\r\n\0'):
            raise ConfigurationError('API keys must be single-line strings.')
    return settings


def available_port(preferred):
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', preferred))
        except OSError:
            probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def stop_tree(process):
    """Only stop a process started by this launcher, including its MCP children."""
    if process is None or process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'),
                        '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=15)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)


class Launcher:
    def __init__(self, directory):
        self.home = Path(directory).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.settings = dict(DEFAULTS)
        path = self.home / 'settings.json'
        if path.exists():
            try:
                self.settings = validate_settings(json.loads(path.read_text(encoding='utf-8')))
            except (ValueError, OSError):
                pass
        self.state = dict(phase='idle', message='Choose settings, then start the demo.', url='', port=None)
        self.process = None
        self.worker = None
        self.cancel = threading.Event()

    def snapshot(self):
        with self.lock:
            keys = read_keys(self.home)
            return dict(**self.state, settings=dict(self.settings),
                        chat_key_configured=bool(keys[KEYS[0]]), manual_key_configured=bool(keys[KEYS[1]]))

    def start(self, body):
        settings = validate_settings(body)
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ConfigurationError('A demo is already starting or running. Stop it before restarting.')
            if settings['pool'] == 'custom':
                path = Path(settings['custom_config']).expanduser().resolve()
                load_config(path)
            keys = read_keys(self.home)
            for field, name in zip(('chat_key', 'manual_key'), KEYS):
                if body.get(field):
                    keys[name] = body[field].strip()
            # With only one key supplied, use it for both. Separate keys remain independent.
            if not keys[KEYS[1]]:
                keys[KEYS[1]] = keys[KEYS[0]]
            if settings['pool'] != 'custom' and not settings['offline']:
                if not keys[KEYS[0]]:
                    raise ConfigurationError('Enter a chat API key, or select the scripted preview.')
                if settings['generate'] and settings['enabled'] and not keys[KEYS[1]]:
                    raise ConfigurationError('Manual generation requires an API key.')
            if settings['pool'] != 'custom':
                # No shell interpolation. Credential values never enter settings/status/logs.
                credential_path = self.home / '.env'
                temporary = self.home / '.env.tmp'
                temporary.write_text(''.join(f'{name}={keys[name]}\n' for name in KEYS), encoding='utf-8')
                temporary.chmod(0o600)
                temporary.replace(credential_path)
            self.settings = settings
            atomic_json(self.home / 'settings.json', settings)
            self.cancel = threading.Event()
            self.state = dict(phase='starting', message='Preparing the Web runtime...', url='', port=None)
            self.worker = threading.Thread(target=self._run, args=(settings, keys), daemon=True)
            self.worker.start()

    def _spawn(self, command, env, cwd):
        with self.lock:
            if self.cancel.is_set():
                return None
            self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                start_new_session=os.name != 'nt')
            return self.process

    def _run(self, settings, keys):
        runtime_config = None
        try:
            if settings['pool'] == 'custom':
                source = Path(settings['custom_config']).expanduser().resolve()
            else:
                home = self.home / settings['pool']
                initialize(home, settings['pool'])
                source = home / 'toollery.json'
            config, home = load_config(source)
            env = os.environ.copy()
            if settings['pool'] != 'custom':
                config.update(model=settings['chat_model'], base_url=settings['chat_url'])
                manual_path = home / 'manual-generator.json'
                manual = json.loads(manual_path.read_text(encoding='utf-8'))
                manual['model'] = settings['manual_model']
                manual['parameters']['api_url'] = settings['manual_url']
                atomic_json(manual_path, manual)
                env.update(keys)
                env.pop('TOOLLERY_API_KEY', None)
            # New DSH sessions honor launch settings; manual cache and playground persist.
            run_id = uuid.uuid4().hex
            run_home = '.toollery/launcher/' + run_id
            config.update(web_home=run_home + '/dsh', state_dir=run_home + '/state')
            workspace = home / ('playground' if settings['pool'] == 'filesystem' else 'demo-workspace')
            workspace.mkdir(exist_ok=True)
            config['_launcher_workspace'] = str(workspace)
            runtime_config = home / ('.toollery-launch-' + run_id + '.json')
            atomic_json(runtime_config, config)
            command = [sys.executable, '-m', 'toollery_dsh']
            try:
                runtime_entry(home)
            except ConfigurationError:
                setup = self._spawn([*command, 'setup-web', '--config', str(runtime_config)], env, home)
                if setup is None:
                    return
                # Drain output without reflecting npm/provider content or credentials into UI.
                timeout = threading.Timer(600, stop_tree, args=(setup,))
                timeout.daemon = True
                timeout.start()
                try:
                    for _ in setup.stdout:
                        pass
                    code = setup.wait()
                finally:
                    timeout.cancel()
                    setup.stdout.close()
                if code != 0:
                    raise ConfigurationError('Web dependency installation failed. Check network access; retry Start demo.')
            if self.cancel.is_set():
                return
            port = available_port(settings['port'])
            args = [*command, 'web', '--config', str(runtime_config), '--port', str(port), '--no-open',
                    '--mode', settings['mode'], '--budget', settings['budget'], '--k', str(settings['k'])]
            if not settings['enabled']:
                args.append('--toollery-off')
            if settings['offline']:
                args.append('--offline')
            elif settings['generate']:
                args.append('--generate-manuals')
            with self.lock:
                self.state.update(message='Starting DSH...', port=port)
            process = self._spawn(args, env, home)
            if process is None:
                return
            for line in process.stdout:
                # Only admit the expected local authenticated URL; never reflect raw logs.
                match = re.search(r'dsh web: (http://127\.0\.0\.1:' + str(port) + r'/\?token=[A-Za-z0-9._~%=-]+)', line)
                with self.lock:
                    if match and not self.cancel.is_set():
                        self.state.update(phase='ready', message='DSH is ready. Open the demo below.', url=match[1])
                    elif 'Session not ready (' in line:
                        self.state.update(message='Manual preparation failed. If cache is missing, stop and enable generation; otherwise check API configuration and preparation reports.')
                    elif line.startswith('Ready: ') and re.fullmatch(r'Ready: \d+/\d+ manuals; \d+ cache hits; \d+ generated\.\s*', line):
                        self.state.update(message=line.strip())
            code = process.wait()
            if not self.cancel.is_set():
                with self.lock:
                    self.state.update(phase='error' if code else 'stopped', url='',
                        message=f'DSH exited (code {code}). Check configuration and the .toollery/runs reports.')
        except Exception as error:
            with self.lock:
                self.state.update(phase='error', url='', message=str(error) if isinstance(error, ConfigurationError)
                                  else f'Launch failed ({type(error).__name__}). Check configuration and retry.')
        finally:
            stop_tree(self.process)
            if self.process and self.process.stdout:
                self.process.stdout.close()
            if runtime_config:
                runtime_config.unlink(missing_ok=True)

    def stop(self):
        with self.lock:
            self.cancel.set()
            process, worker = self.process, self.worker
        stop_tree(process)
        if worker and worker is not threading.current_thread():
            worker.join(timeout=20)
        with self.lock:
            self.state.update(phase='stopped', message='Stopped. Settings and cached manuals are retained.', url='')


def make_server(launcher, port=0):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, value, content_type='application/json'):
            data = json.dumps(value).encode() if content_type == 'application/json' else value
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            return (self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'
                    and hmac.compare_digest(self.headers.get('X-Toollery-Token', '').encode('utf-8'), token.encode('ascii')))

        def do_GET(self):
            if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                return self.reply(403, {'error': 'Invalid host.'})
            if self.path in ('/', '/app.js'):
                name, kind = ('launcher.html', 'text/html; charset=utf-8') if self.path == '/' else ('launcher.js', 'text/javascript; charset=utf-8')
                return self.reply(200, (ASSETS / name).read_bytes(), kind)
            if self.path == '/api/status' and self.authorized():
                return self.reply(200, launcher.snapshot())
            self.reply(403, {'error': 'Open the setup URL printed in the terminal.'})

        def do_POST(self):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if not self.authorized() or self.headers.get('Origin') != origin:
                return self.reply(403, {'error': 'Access denied.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 32768:
                    return self.reply(413, {'error': 'Request too large or empty.'})
                body = json.loads(self.rfile.read(length))
                if self.path == '/api/start':
                    launcher.start(body)
                elif self.path == '/api/stop':
                    launcher.stop()
                else:
                    return self.reply(404, {'error': 'Unknown action.'})
                self.reply(200, launcher.snapshot())
            except (ValueError, OSError) as error:
                self.reply(400, {'error': str(error) if isinstance(error, ConfigurationError) else 'Invalid request or inaccessible configuration.'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server, token


def run(args):
    # A lifetime lock prevents two setup windows from sharing and modifying one cache/config.
    launcher = Launcher(args.directory)
    lock_file = (launcher.home / 'launcher.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock_file.seek(0)
            if not lock_file.read(1):
                lock_file.write(b'0'); lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        raise ConfigurationError('Another launcher uses this directory. Use its setup page or close it first.') from None
    server = None
    try:
        server, token = make_server(launcher, args.port)
        url = f'http://127.0.0.1:{server.server_port}/#token={token}'
        print(f'Toollery setup: {url}\nKeep this terminal open. Ctrl+C stops the launcher and demo.', flush=True)
        if not args.no_open:
            webbrowser.open(url)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        launcher.stop()
        if server:
            server.server_close()
        lock_file.close()
    return 0
