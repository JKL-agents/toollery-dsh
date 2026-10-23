from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from .data import ToolPool, ToolRecord
from .evidence import NORMALIZATION_VERSION, Evidence, normalize
from .generation import Generator, PROMPT_VERSION, ProviderFailure, validate_entries, validate_queries
from .storage import atomic_json, digest


@dataclass
class PreparedManuals:
    records: dict[str, dict]
    report: dict


class PreparationError(RuntimeError):
    def __init__(self, report: dict):
        self.report = report
        super().__init__(f"Manual pool not ready: {report['valid']}/{report['total']} tools valid")


def cache_preflight(
    pool: ToolPool,
    generator: Generator,
    cache: ManualCache,
    *,
    count: int = 3,
    evidence_limit: int = 24000,
) -> dict:
    """Check manual identities without making provider calls.

    This deliberately mirrors the identity path used by ``prepare_manuals``
    so refresh thresholds count real cache misses, including schema or
    generator-contract changes, rather than merely counting changed tools.
    """
    if count < 1 or count > 64 or evidence_limit < 256:
        raise ValueError("Invalid preflight bounds")
    valid, missing, failures = 0, [], []
    for tool_id in pool.tool_ids:
        try:
            tool = pool.tools[tool_id]
            evidence = normalize(tool, evidence_limit)
            identity = cache_identity(tool, evidence, generator, count)
            if cache.read(identity) is None:
                missing.append(tool_id)
            else:
                valid += 1
        except Exception as error:
            failures.append({"tool_id": tool_id, "reason": type(error).__name__})
    return {
        "total": len(pool.tools),
        "valid": valid,
        "missing": len(missing),
        "missing_tool_ids": missing,
        "failures": failures,
        "proxy_query_count": count,
        "evidence_limit": evidence_limit,
        "generator": generator.contract,
    }


def cache_identity(tool: ToolRecord, evidence: Evidence, generator: Generator, count: int) -> dict:
    return {
        "format_version": 2,
        "tool_id": tool.tool_id,
        "spec_hash": tool.spec_hash,
        "evidence_hash": evidence.evidence_hash,
        "normalization_version": NORMALIZATION_VERSION,
        "manual_prompt_version": PROMPT_VERSION,
        "generator": generator.contract,
        "proxy_query_count": count,
        "language_policy": "paper_prompt_unspecified",
    }


class ManualCache:
    def __init__(self, root: Path):
        self.root = root

    def path(self, key: str) -> Path:
        return self.root / "manuals" / (key.removeprefix("sha256:") + ".json")

    def read(self, identity: dict) -> dict | None:
        key = digest(identity)
        path = self.path(key)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or any(record.get(k) != v for k, v in identity.items()):
                return None
            if record.get("manual_key") != key:
                return None
            queries = validate_queries(record["manual"]["proxy_queries"], identity["proxy_query_count"])
            entries = validate_entries(record["manual"]["entries"], identity["proxy_query_count"])
            if entries != record["manual"]["entries"] or [entry["query"] for entry in entries] != queries:
                return None
            if queries != record["manual"]["proxy_queries"] or record.get("manual_hash") != digest(record["manual"]):
                return None
            return record
        except (OSError, ValueError, TypeError, KeyError):
            return None

    @contextmanager
    def lock(self, key: str):
        path = self.path(key).with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        # An existing lock makes this preparation fail rather than duplicate a paid call.
        with path.open("x", encoding="utf-8") as stream:
            stream.write(datetime.now(timezone.utc).isoformat())
        try:
            yield
        finally:
            path.unlink(missing_ok=True)

    def write(self, identity: dict, evidence: Evidence, entries: list[dict[str, str]], usage: dict) -> dict:
        key = digest(identity)
        entries = validate_entries(entries, identity["proxy_query_count"])
        manual = {"entries": entries, "proxy_queries": [entry["query"] for entry in entries]}
        record = {
            **identity,
            "manual_key": key,
            "source_mode": evidence.source_mode,
            "manual": manual,
            "manual_hash": digest(manual),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "usage": usage,
        }
        atomic_json(self.path(key), record)
        return record


def prepare_manuals(
    pool: ToolPool,
    generator: Generator,
    cache: ManualCache,
    *,
    count: int = 3,
    evidence_limit: int = 24000,
    attempts: int = 2,
    cache_only: bool = False,
    max_new: int | None = None,
    progress=None,
) -> PreparedManuals:
    if count < 1 or count > 64 or attempts < 1 or evidence_limit < 256 or (max_new is not None and max_new < 0):
        raise ValueError("Invalid preparation bounds")
    started = time.perf_counter()
    records, failures, usage_totals = {}, [], Counter()
    hits = generated = calls = attempted_tools = 0
    for index, tool_id in enumerate(pool.tool_ids, 1):
        tool = pool.tools[tool_id]
        try:
            evidence = normalize(tool, evidence_limit)
            identity = cache_identity(tool, evidence, generator, count)
            record = cache.read(identity)
            if record is not None:
                hits += 1
            elif cache_only or (max_new is not None and attempted_tools >= max_new):
                failures.append({"tool_id": tool_id, "reason": "missing_or_invalid_cache"})
                continue
            else:
                attempted_tools += 1
                with cache.lock(digest(identity)):
                    record = cache.read(identity)  # Another preparer may have finished before this lock.
                    if record is not None:
                        hits += 1
                    else:
                        for attempt in range(attempts):
                            try:
                                calls += 1
                                result = generator.generate(evidence, count)
                                entries = validate_entries(result.entries, count)
                                record = cache.write(identity, evidence, entries, result.usage)
                                generated += 1
                                for name, value in result.usage.items():
                                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                                        usage_totals[name] += value
                                break
                            except Exception:
                                if attempt + 1 == attempts:
                                    raise
            records[tool_id] = record
        except Exception as error:
            # Errors are class-only so provider output/credentials do not enter reports.
            failures.append({"tool_id": tool_id, "reason": type(error).__name__, **({"code": error.safe_code} if isinstance(error, ProviderFailure) else {})})
        finally:
            if progress is not None and (index % 500 == 0 or index == len(pool.tools)):
                progress(index, len(pool.tools), generated, hits, len(failures))
    report = {
        "ready": len(records) == len(pool.tools),
        "snapshot_hash": pool.snapshot_hash,
        "total": len(pool.tools), "valid": len(records), "cache_hits": hits,
        "generated": generated, "generation_calls": calls,
        "attempted_tools": attempted_tools,
        "failures": failures, "usage_from_new_generations": dict(usage_totals),
        "elapsed_seconds": time.perf_counter() - started,
        "generator": generator.contract,
        "proxy_query_count": count, "evidence_limit": evidence_limit,
    }
    if not report["ready"]:
        raise PreparationError(report)
    report["manual_pool_hash"] = digest([(tool_id, records[tool_id]["manual_key"], records[tool_id]["manual_hash"]) for tool_id in pool.tool_ids])
    return PreparedManuals(records, report)
