from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path

from .storage import canonical, digest, file_digest, read_jsonl


@dataclass(frozen=True)
class ToolRecord:
    tool_id: str
    documentation: object

    @property
    def spec_hash(self) -> str:
        return digest(self.documentation)

    @property
    def spec_text(self) -> str:
        return self.documentation if isinstance(self.documentation, str) else canonical(self.documentation)

    @property
    def name(self) -> str:
        value = self.documentation.get("name") if isinstance(self.documentation, dict) else None
        return value.strip() if isinstance(value, str) else ""


def require_id(row: dict, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing or invalid {key}")
    return value


class ToolPool:
    def __init__(self, records: list[ToolRecord]):
        self.tools = {record.tool_id: record for record in records}
        if not records or len(self.tools) != len(records):
            raise ValueError("Tool pool must be nonempty and contain unique IDs")
        self.tool_ids = tuple(sorted(self.tools))
        self.snapshot_hash = digest([(key, self.tools[key].spec_hash) for key in self.tool_ids])

    @classmethod
    def load(cls, path: Path) -> ToolPool:
        records = []
        for row in read_jsonl(path):
            tool_id = require_id(row, "id")
            if "documentation" not in row:
                raise ValueError(f"Missing documentation for {tool_id}")
            documentation = row["documentation"]
            if isinstance(documentation, str):
                try:
                    documentation = json.loads(documentation)
                except ValueError:
                    pass  # Plain text is valid input; do not invent a description.
            if documentation is None:
                raise ValueError(f"Empty documentation for {tool_id}")
            records.append(ToolRecord(tool_id, documentation))
        return cls(records)


@dataclass(frozen=True)
class Query:
    query_id: str
    text: str
    source_dataset: str


@dataclass
class Dataset:
    pool: ToolPool
    queries: list[Query]
    qrels: dict[str, dict[str, float]]
    provenance: dict

    @classmethod
    def load(cls, directory: Path) -> Dataset:
        pool = ToolPool.load(directory / "tools.jsonl")
        queries = []
        query_ids = set()
        for row in read_jsonl(directory / "queries.jsonl"):
            query_id = require_id(row, "id")
            if query_id in query_ids or not isinstance(row.get("query"), str):
                raise ValueError(f"Duplicate ID or invalid query: {query_id}")
            query_ids.add(query_id)
            # Deliberately do not copy labels, instruction or category into retrieval inputs.
            queries.append(Query(query_id, row["query"], str(row.get("source_dataset", "unknown"))))
        if not queries:
            raise ValueError("Query set is empty")
        qrels: dict[str, dict[str, float]] = {query_id: {} for query_id in query_ids}
        seen = set()
        for row in read_jsonl(directory / "qrels.jsonl"):
            query_id, tool_id = require_id(row, "query_id"), require_id(row, "tool_id")
            if query_id not in query_ids or tool_id not in pool.tools:
                raise ValueError(f"Dangling qrel: {query_id} -> {tool_id}")
            if (query_id, tool_id) in seen:
                raise ValueError(f"Duplicate qrel: {query_id} -> {tool_id}")
            seen.add((query_id, tool_id))
            relevance = float(row["relevance"])
            if not math.isfinite(relevance) or relevance < 0:
                raise ValueError("Relevance must be finite and nonnegative")
            if relevance > 0:
                qrels[query_id][tool_id] = relevance
        if any(not labels for labels in qrels.values()):
            raise ValueError("Every evaluation query must have a positive qrel")
        provenance = {
            "directory": str(directory.resolve()),
            "files": {name: file_digest(directory / name) for name in ("tools.jsonl", "queries.jsonl", "qrels.jsonl")},
        }
        return cls(pool, queries, qrels, provenance)
