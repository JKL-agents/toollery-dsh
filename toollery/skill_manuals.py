"""Skill-body evidence, manual cache identities and preparation helpers.

Skill manuals deliberately live beside, but separately from, tool manuals. A
skill body is host-owned content: the worker receives it only through the
native loader boundary and uses a bounded evidence projection for generation.
The full body hash remains part of the identity so a body change invalidates
the generated manual without putting the body in retrieval results.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable
import time

from .evidence import Evidence, meaningful
from .generation import Generator, PROMPT_VERSION, ProviderFailure, validate_entries
from .manuals import ManualCache, PreparedManuals, PreparationError
from .storage import canonical, digest


SKILL_NORMALIZATION_VERSION = "skill_body_evidence_v1"
DEFAULT_PROXY_QUERY_COUNT = 3
DEFAULT_EVIDENCE_LIMIT = 24000
MAX_SKILL_BODY_CHARACTERS = 1_000_000


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return canonical(value)


def normalize_skill(skill: dict, max_characters: int = DEFAULT_EVIDENCE_LIMIT) -> Evidence:
    """Build bounded generation evidence while hashing the complete source body."""
    if not isinstance(skill, dict):
        raise ValueError("Invalid skill candidate")
    name = skill.get("name")
    description = skill.get("description", "")
    content = skill.get("content")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Invalid skill name")
    if not isinstance(description, str):
        raise ValueError("Invalid skill description")
    if not isinstance(content, str) or not content.strip() or len(content) > MAX_SKILL_BODY_CHARACTERS:
        raise ValueError("Skill body is required for manual generation")
    if max_characters < 256:
        raise ValueError("Evidence limit must be at least 256 characters")

    source = {"name": name.strip(), "description": description.strip(), "content": content}
    for key in ("when_to_use", "tags"):
        if key in skill and meaningful(skill[key]):
            source[key] = skill[key]
    fields, truncated, remaining = {}, [], max_characters
    # Keep identity/name and the catalog summary, then spend the remaining
    # budget on the actual body. The complete body is still hashed separately.
    for key in ("name", "content", "description", "when_to_use", "tags"):
        if key not in source or not meaningful(source[key]):
            continue
        value = source[key]
        text = _text(value)
        size = len(key) + len(text)
        if size <= remaining:
            fields[key] = value.strip() if isinstance(value, str) else value
            remaining -= size
        else:
            truncated.append(key)
            if remaining > len(key):
                fields[key] = text[: remaining - len(key)]
                remaining = 0
    if "content" not in fields:
        raise ValueError("Skill body exceeds the evidence limit")
    return Evidence(fields, "skill_body", tuple(truncated), max_characters)


def _metadata(skill: dict) -> dict:
    return {
        key: skill.get(key)
        for key in ("name", "description", "when_to_use", "tags")
        if key in skill and meaningful(skill[key])
    }


def skill_cache_identity(skill: dict, evidence: Evidence, generator: Generator,
                         count: int) -> dict:
    name = skill["name"].strip()
    content = skill["content"]
    return {
        "format_version": 1,
        "manual_kind": "skill",
        "skill_name": name,
        "skill_body_hash": digest(content),
        "skill_metadata_hash": digest(_metadata(skill)),
        "evidence_hash": evidence.evidence_hash,
        "normalization_version": SKILL_NORMALIZATION_VERSION,
        "manual_prompt_version": PROMPT_VERSION,
        "generator": generator.contract,
        "proxy_query_count": count,
        "language_policy": "paper_prompt_unspecified",
    }


def _validate_candidates(candidates: Iterable[dict]) -> list[dict]:
    normalized = []
    seen = set()
    for item in candidates:
        if not isinstance(item, dict):
            raise ValueError("Invalid skill candidate")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid skill name")
        name = name.strip()
        if name in seen:
            raise ValueError("Skill names must be unique")
        description = item.get("description", "")
        if not isinstance(description, str):
            raise ValueError("Invalid skill description")
        content = item.get("content")
        if (not isinstance(content, str) or not content.strip()
                or len(content) > MAX_SKILL_BODY_CHARACTERS):
            raise ValueError("Skill body is required for manual generation")
        record = {"name": name, "description": description}
        for key in ("when_to_use", "tags"):
            if key in item and item[key] is not None:
                if key == "when_to_use" and not isinstance(item[key], str):
                    raise ValueError("Invalid skill when_to_use")
                if key == "tags" and not isinstance(item[key], (str, list, dict)):
                    raise ValueError("Invalid skill tags")
                record[key] = item[key]
        record["content"] = content
        normalized.append(record)
        seen.add(name)
    return normalized


def skill_cache_preflight(candidates: list[dict], generator: Generator, cache: ManualCache,
                          *, count: int = DEFAULT_PROXY_QUERY_COUNT,
                          evidence_limit: int = DEFAULT_EVIDENCE_LIMIT) -> dict:
    if count < 1 or count > 64 or evidence_limit < 256:
        raise ValueError("Invalid skill preflight bounds")
    valid, missing, failures = 0, [], []
    for skill in _validate_candidates(candidates):
        try:
            evidence = normalize_skill(skill, evidence_limit)
            identity = skill_cache_identity(skill, evidence, generator, count)
            if cache.read(identity) is None:
                missing.append(skill["name"])
            else:
                valid += 1
        except Exception as error:
            failures.append({"skill_name": skill.get("name"), "reason": type(error).__name__})
    return {
        "total": len(candidates),
        "valid": valid,
        "missing": len(missing),
        "missing_skill_names": missing,
        "failures": failures,
        "proxy_query_count": count,
        "evidence_limit": evidence_limit,
        "generator": generator.contract,
    }


def prepare_skill_manuals(
    candidates: list[dict],
    generator: Generator,
    cache: ManualCache,
    *,
    count: int = DEFAULT_PROXY_QUERY_COUNT,
    evidence_limit: int = DEFAULT_EVIDENCE_LIMIT,
    attempts: int = 2,
    cache_only: bool = False,
    progress=None,
) -> PreparedManuals:
    if count < 1 or count > 64 or attempts < 1 or evidence_limit < 256:
        raise ValueError("Invalid skill preparation bounds")
    candidates = _validate_candidates(candidates)
    started = time.perf_counter()
    records, failures, usage_totals = {}, [], Counter()
    hits = generated = calls = attempted = 0
    for index, skill in enumerate(candidates, 1):
        try:
            evidence = normalize_skill(skill, evidence_limit)
            identity = skill_cache_identity(skill, evidence, generator, count)
            record = cache.read(identity)
            if record is not None:
                hits += 1
            elif cache_only:
                failures.append({"skill_name": skill["name"], "reason": "missing_or_invalid_cache"})
            else:
                attempted += 1
                with cache.lock(digest(identity)):
                    record = cache.read(identity)
                    if record is not None:
                        hits += 1
                    else:
                        for attempt in range(attempts):
                            try:
                                calls += 1
                                result = generator.generate(evidence, count)
                                entries = validate_entries(result.entries, count)
                                record = cache.write(
                                    identity, evidence, entries, result.usage,
                                    provenance={
                                        "kind": "skill",
                                        "skill_name": skill["name"],
                                        "skill_body_hash": identity["skill_body_hash"],
                                        "skill_metadata_hash": identity["skill_metadata_hash"],
                                    })
                                generated += 1
                                for name, value in result.usage.items():
                                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                                        usage_totals[name] += value
                                break
                            except Exception:
                                if attempt + 1 == attempts:
                                    raise
            if record is not None:
                records[skill["name"]] = record
        except Exception as error:
            failures.append({"skill_name": skill.get("name"), "reason": type(error).__name__,
                             **({"code": error.safe_code} if isinstance(error, ProviderFailure) else {})})
        finally:
            if progress is not None:
                progress(index, len(candidates), generated, hits, len(failures))
    report = {
        "ready": len(records) == len(candidates),
        "manual_kind": "skill",
        "total": len(candidates), "valid": len(records), "cache_hits": hits,
        "generated": generated, "generation_calls": calls, "attempted_skills": attempted,
        "failures": failures, "usage_from_new_generations": dict(usage_totals),
        "elapsed_seconds": time.perf_counter() - started, "generator": generator.contract,
        "proxy_query_count": count, "evidence_limit": evidence_limit,
    }
    if not report["ready"]:
        raise PreparationError(report)
    report["manual_pool_hash"] = digest([
        (name, records[name]["manual_key"], records[name]["manual_hash"])
        for name in sorted(records)
    ])
    return PreparedManuals(records, report)


def validate_skill_candidates(candidates: list[dict], *, require_content: bool = True) -> list[dict]:
    """Public worker boundary validator; metadata-only spec mode may omit body."""
    if not isinstance(candidates, list) or len(candidates) > 4096:
        raise ValueError("Skill catalog must contain 0..4096 candidates")
    if require_content:
        return _validate_candidates(candidates)
    normalized, seen = [], set()
    for item in candidates:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError("Invalid skill candidate")
        name = item["name"].strip()
        if name in seen:
            raise ValueError("Skill names must be unique")
        description = item.get("description", "")
        if not isinstance(description, str):
            raise ValueError("Invalid skill description")
        record = {"name": name, "description": description}
        for key in ("when_to_use", "tags"):
            if key in item and item[key] is not None:
                record[key] = item[key]
        normalized.append(record)
        seen.add(name)
    return normalized
