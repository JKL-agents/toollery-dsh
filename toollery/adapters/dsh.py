"""Local transport for the DSH plugin. No DSH SDK imports are required here."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets
import threading
import time

from ..data import ToolPool, ToolRecord
from ..manuals import ManualCache, PreparationError, prepare_manuals
from ..retrieval import BM25Index, REPRESENTATIONS, ToolSearch, build_documents
from ..storage import atomic_json, canonical, digest


def snapshot_pool(schemas: list[dict]) -> ToolPool:
    """Use DSH's actual model-facing schemas, preserving the original spec."""
    if not isinstance(schemas, list):
        raise ValueError("Invalid tool snapshot")
    records = []
    for schema in schemas:
        if (not isinstance(schema, dict) or not isinstance(schema.get("name"), str)
                or not schema["name"] or not isinstance(schema.get("description"), str)
                or not isinstance(schema.get("parameters"), dict)):
            raise ValueError("Invalid DSH tool schema")
        # Detach from callers; names are already unique in the DSH scope.
        records.append(ToolRecord(schema["name"], json.loads(canonical(schema))))
    return ToolPool(records)


@dataclass
class SessionState:
    pool: ToolPool
    searches: dict[str, ToolSearch] = field(default_factory=dict)
    preparation: dict = field(default_factory=dict)
    representation: str = "spec_manual"
    budget: str = "top_k"
    k: int = 3
    revision: int = 0
    ready: bool = False
    enabled: bool = True


class DSHBridge:
    """A frozen full pool per DSH agent, with per-prompt selections.

    Discovery and preparation are explicit. A missing/stale manual never
    silently switches a tool to spec-only retrieval. The runtime plugin filters
    schemas and guards execution; this service only prepares and searches.
    """

    def __init__(self, output_dir: Path, *, representation="spec_manual", budget="top_k", k=3, on_selection=None,
                 on_snapshot=None, enabled=True, state_dir=None, compact_audit=False):
        self.validate_options(representation, budget, k)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        self.output_dir = output_dir
        self.defaults = representation, budget, k
        self.default_enabled = enabled
        self.state_dir = Path(state_dir) if state_dir else None
        self.compact_audit = compact_audit
        self.calls: list[dict] = []
        self.on_selection = on_selection
        self.on_snapshot = on_snapshot
        self.sessions: dict[str, SessionState] = {}
        self.selections: list[dict] = []
        self.observations: list[dict] = []
        self._lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.thread = None

    @staticmethod
    def validate_options(representation, budget, k):
        if representation not in REPRESENTATIONS or budget not in {"all", "top_k"}:
            raise ValueError("Invalid retrieval mode")
        if isinstance(k, bool) or not isinstance(k, int) or k < 1:
            raise ValueError("k must be a positive integer")

    def capture(self, session_id: str, schemas: list[dict], discovery=None) -> dict:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Invalid session identity")
        if discovery is not None and discovery.get('complete') is not True:
            raise ValueError('Tool-source discovery is incomplete')
        pool = snapshot_pool(schemas)
        with self._lock:
            previous = self.sessions.get(session_id)
            if previous is not None and previous.pool.snapshot_hash != pool.snapshot_hash:
                raise ValueError("Tool snapshot changed; prepare a new session")
            if previous is None:
                rep, budget, k = self.defaults
                self.sessions[session_id] = SessionState(pool, representation=rep, budget=budget, k=k,
                                                        enabled=self.default_enabled)
                if self.state_dir:
                    file = self._state_file(session_id)
                    if file.is_file():
                        saved = json.loads(file.read_text(encoding='utf-8'))
                        self.selections.extend(saved.get('selections', []))
                        self.calls.extend(saved.get('calls', []))
                        if saved.get('snapshot_hash') == pool.snapshot_hash:
                            options = saved['options']
                            self.validate_options(options['representation'], options['budget'], options['k'])
                            state = self.sessions[session_id]
                            state.representation, state.budget, state.k = options['representation'], options['budget'], options['k']
                            state.enabled = options['enabled']
                            state.revision = options.get('revision', 0)
            # Use hashed session names so an SDK session ID is never a path.
            filename = digest(session_id).removeprefix("sha256:")[:20]
            atomic_json(self.output_dir / f"snapshot-{filename}.json", {
                "session_id": session_id, "snapshot_hash": pool.snapshot_hash,
                "tool_count": len(pool.tools),
                "discovery": discovery,
                "schemas": [pool.tools[key].documentation for key in pool.tool_ids],
            })
        return {"snapshot_hash": pool.snapshot_hash, "tool_count": len(pool.tools)}

    def prepare(self, session_id, generator, cache: ManualCache, *, workers=4, cache_only=False,
                with_manuals=True, progress=None):
        if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
            raise ValueError("workers must be 1..8")
        started = time.perf_counter()
        with self._lock:
            state = self.sessions[session_id]
            # A failed re-preparation must not leave an old ready state active.
            state.ready = False
            state.searches = {}
            if not with_manuals and state.representation != "spec":
                raise ValueError("Spec-only preparation requires spec mode")
        manuals, reports = {}, []
        if with_manuals:
            # Parallelize independent tools using the existing cache identities
            # and paid-request locks. Do not change the generation contract.
            def one(tool_id):
                try:
                    return prepare_manuals(ToolPool([state.pool.tools[tool_id]]), generator, cache,
                                           count=3, cache_only=cache_only)
                except PreparationError as error:
                    return error

            with ThreadPoolExecutor(max_workers=workers) as executor:
                for index, result in enumerate(executor.map(one, state.pool.tool_ids), 1):
                    if isinstance(result, PreparationError):
                        reports.append(result.report)
                    else:
                        manuals.update(result.records)
                        reports.append(result.report)
                    if progress:
                        progress(index, len(state.pool.tools))
            report = {
                "ready": len(manuals) == len(state.pool.tools),
                "snapshot_hash": state.pool.snapshot_hash,
                "total": len(state.pool.tools), "valid": len(manuals),
                "cache_hits": sum(r["cache_hits"] for r in reports),
                "generated": sum(r["generated"] for r in reports),
                "generation_calls": sum(r["generation_calls"] for r in reports),
                "failures": [f for r in reports for f in r["failures"]],
                "proxy_query_count": 3, "workers": workers,
                "generator": generator.contract,
                "usage_from_new_generations": {},
                "evidence_limit": 24000,
                "elapsed_seconds": time.perf_counter() - started,
            }
            for item in reports:
                for key, value in item["usage_from_new_generations"].items():
                    report["usage_from_new_generations"][key] = report["usage_from_new_generations"].get(key, 0) + value
            if report["ready"]:
                report["manual_pool_hash"] = digest([(key, manuals[key]["manual_key"], manuals[key]["manual_hash"]) for key in state.pool.tool_ids])
            atomic_json(self.output_dir / "manual-preparation.json", report)
            if not report["ready"]:
                raise PreparationError(report)
        else:
            report = {"ready": True, "manuals_prepared": False, "total": len(state.pool.tools)}
            atomic_json(self.output_dir / "manual-preparation.json", report)
        searches = {}
        for representation in REPRESENTATIONS if with_manuals else ("spec",):
            documents = build_documents(state.pool, representation, manuals if with_manuals else None)
            searches[representation] = ToolSearch(state.pool, BM25Index(documents))
        with self._lock:
            state.searches = searches
            state.preparation = report
            state.ready = True
        return report

    def configure(self, session_id, *, representation=None, budget=None, k=None, enabled=None):
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        with self._lock:
            state = self.sessions[session_id]
            self.validate_options(representation if representation is not None else state.representation,
                                  budget if budget is not None else state.budget,
                                  k if k is not None else state.k)
            needs_preparation = enabled is True and not state.ready
        # Do not hold the bridge lock while entering the launcher's preparation
        # lock: another snapshot may already be preparing in the opposite order.
        if needs_preparation and self.on_snapshot:
            self.on_snapshot(self, session_id)
        with self._lock:
            state = self.sessions[session_id]
            rep = representation if representation is not None else state.representation
            limit = budget if budget is not None else state.budget
            count = k if k is not None else state.k
            self.validate_options(rep, limit, count)
            # Prepare before committing ON. Failure leaves OFF and its full pool
            # usable; no silent spec fallback and no partially applied settings.
            if enabled is True and not state.ready:
                raise RuntimeError("Manuals and index are not ready")
            if state.ready and rep not in state.searches:
                raise ValueError("Manuals were not prepared for this mode")
            state.representation, state.budget, state.k = rep, limit, count
            if enabled is not None:
                state.enabled = enabled
            state.revision += 1
            self._persist(session_id)

    def _state_file(self, session_id):
        return self.state_dir / (digest(session_id).removeprefix('sha256:') + '.json')

    def _persist(self, session_id):
        if not self.state_dir:
            return
        state = self.sessions[session_id]
        atomic_json(self._state_file(session_id), {
            'snapshot_hash': state.pool.snapshot_hash,
            'options': {key: getattr(state, key) for key in ('enabled', 'representation', 'budget', 'k', 'revision')},
            'selections': [s for s in self.selections if s['session_id'] == session_id],
            'calls': [c for c in self.calls if c['session_id'] == session_id],
        })

    def select(self, payload: dict) -> dict:
        session_id = payload["session_id"]
        prompt = payload["prompt"]
        if not isinstance(prompt, str):
            raise ValueError("A text prompt is required")
        with self._lock:
            state = self.sessions[session_id]
            if state.enabled and not state.ready:
                raise RuntimeError("Manuals and index are not ready")
            if payload["snapshot_hash"] != state.pool.snapshot_hash:
                raise ValueError("Stale tool snapshot")
            if state.enabled and state.representation not in state.searches:
                raise ValueError("Selected representation is not prepared")
            started = time.perf_counter()
            ranked = (state.searches[state.representation].select(prompt, budget=state.budget, k=state.k)
                      if state.enabled else [])
            result = {
                "session_id": session_id, "snapshot_hash": state.pool.snapshot_hash,
                "prompt": prompt, "message_ids": payload.get("message_ids", []),
                "turn": payload.get("turn"), "representation": state.representation,
                "budget": state.budget, "k": state.k, "revision": state.revision,
                "enabled": state.enabled,
                "full_pool_count": len(state.pool.tools),
                "selected_names": [hit.tool_id for hit in ranked] if state.enabled else list(state.pool.tool_ids),
                "ranked": [{"name": hit.tool_id, "score": hit.score} for hit in ranked],
                "retrieval_ms": (time.perf_counter() - started) * 1000 if state.enabled else 0.0,
            }
            if not self.compact_audit:
                result['tool_details'] = [
                    {'name': key, 'description': state.pool.tools[key].documentation['description']}
                    for key in state.pool.tool_ids]
            result["selection_id"] = digest({key: value for key, value in result.items() if key != "retrieval_ms"})
            self.selections.append(result)
            self._audit('selections', result, self.selections)
            self._persist(session_id)
            if self.on_selection:
                self.on_selection(result)
            return result

    def observe(self, payload):
        with self._lock:
            self.observations.append(payload)
            self._audit('assemblies', payload, self.observations)
        return {"ok": True}

    def _audit(self, name, record, records):
        if self.compact_audit:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with (self.output_dir / (name + '.jsonl')).open('a', encoding='utf-8') as stream:
                stream.write(canonical(record) + '\n')
        else:
            atomic_json(self.output_dir / (name + '.json'), records)

    def record_call(self, payload):
        with self._lock:
            selection = next(s for s in self.selections if s['selection_id'] == payload['selection_id']
                             and s['session_id'] == payload['session_id'])
            if payload['name'] not in selection['selected_names'] or not isinstance(payload['call_id'], str):
                raise ValueError('Call outside selection')
            if payload['status'] not in {'running', 'success', 'error'}:
                raise ValueError('Invalid call status')
            call = {key: payload[key] for key in ('session_id', 'selection_id', 'call_id', 'name', 'status')}
            existing = next((c for c in self.calls if c['selection_id'] == call['selection_id']
                             and c['call_id'] == call['call_id']), None)
            if existing:
                if existing['name'] != call['name']:
                    raise ValueError('Call identity changed')
                existing.update(call)
            else:
                self.calls.append(call)
            self._persist(payload['session_id'])
        return {'ok': True}

    def status(self, session_id):
        with self._lock:
            state = self.sessions[session_id]
            latest = next((s for s in reversed(self.selections) if s['session_id'] == session_id), None)
            return {
                'enabled': state.enabled, 'ready': not state.enabled or state.ready,
                'retrieval_ready': state.ready, 'tool_count': len(state.pool.tools),
                'tool_names': list(state.pool.tool_ids), 'snapshot_hash': state.pool.snapshot_hash,
                'representation': state.representation, 'budget': state.budget, 'k': state.k,
                'manuals_valid': state.preparation.get('valid', 0),
                'last_selection': latest,
                'turns': [{**s, 'calls': [c for c in self.calls if c['selection_id'] == s['selection_id']]}
                          for s in self.selections if s['session_id'] == session_id],
            }

    def __enter__(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + bridge.token):
                    self.reply(401, {"error": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 16 * 1024 * 1024:
                        raise ValueError("Invalid request size")
                    payload = json.loads(self.rfile.read(length))
                    if self.path == "/snapshot":
                        result = bridge.capture(payload["session_id"], payload["schemas"], payload.get('discovery'))
                        if bridge.on_snapshot and bridge.sessions[payload["session_id"]].enabled:
                            bridge.on_snapshot(bridge, payload["session_id"])
                    elif self.path == "/status":
                        result = bridge.status(payload['session_id'])
                    elif self.path == "/configure":
                        bridge.configure(payload['session_id'], **payload['options'])
                        result = ({'ok': True} if bridge.compact_audit
                                  else bridge.status(payload['session_id']))
                    elif self.path == "/select":
                        result = bridge.select(payload)
                    elif self.path == "/observed":
                        result = bridge.observe(payload)
                    elif self.path == "/call":
                        result = bridge.record_call(payload)
                    else:
                        self.reply(404, {"error": "unknown_endpoint"})
                        return
                    self.reply(200, result)
                except (ValueError, TypeError, KeyError):
                    self.reply(409, {"error": "invalid_or_changed_snapshot"})
                except RuntimeError:
                    self.reply(503, {"error": "toollery_not_ready"})
                except Exception:
                    self.reply(500, {"error": "bridge_internal_error"})

            def reply(self, code, value):
                body = canonical(value).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
