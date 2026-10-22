from __future__ import annotations

from dataclasses import dataclass

from .data import ToolRecord
from .storage import canonical, digest

NORMALIZATION_VERSION = "evidence_v1"
EXCLUDED_FIELDS = frozenset({"id", "tool_id", "source_dataset", "instruction", "labels", "category"})
PRIORITY = ("name", "description", "functionality", "parameters", "required_parameters", "optional_parameters", "responses", "returns", "method", "url", "api_call", "example_code")


def meaningful(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() not in {"", "none", "null", "n/a", "na", "unknown"}
    return value is not None and value != {} and value != []


@dataclass(frozen=True)
class Evidence:
    fields: dict[str, object]
    source_mode: str
    truncated_fields: tuple[str, ...]
    max_characters: int

    @property
    def payload(self) -> dict:
        return {
            "fields": self.fields,
            "source_mode": self.source_mode,
            "truncated_fields": list(self.truncated_fields),
            "normalization_version": NORMALIZATION_VERSION,
            "max_characters": self.max_characters,
        }

    @property
    def evidence_hash(self) -> str:
        return digest(self.payload)


def normalize(tool: ToolRecord, max_characters: int = 24000) -> Evidence:
    if max_characters < 256:
        raise ValueError("Evidence limit must be at least 256 characters")
    raw = tool.documentation
    source = raw if isinstance(raw, dict) else {"raw_documentation": raw}
    source_mode = "description" if isinstance(source.get("description"), str) and meaningful(source["description"]) else "structured_fallback"
    keys = list(PRIORITY) + sorted(set(source) - set(PRIORITY) - EXCLUDED_FIELDS)
    fields, truncated, remaining = {}, [], max_characters
    for key in keys:
        if key not in source or key in EXCLUDED_FIELDS or not meaningful(source[key]):
            continue
        value = source[key]
        text = value.strip() if isinstance(value, str) else canonical(value)
        size = len(key) + len(text)
        if size <= remaining:
            fields[key] = value.strip() if isinstance(value, str) else value
            remaining -= size
        else:
            truncated.append(key)
            if remaining > len(key):
                fields[key] = text[: remaining - len(key)]
                remaining = 0
    if not fields:
        raise ValueError(f"No usable documentation evidence for {tool.tool_id}")
    return Evidence(fields, source_mode, tuple(truncated), max_characters)
