"""Build a reproducible ToolRet prototype view that holds out UltraTool."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "benchmark" / "toolret"
ULTRATOOL_SOURCE = "ultratool"
ULTRATOOL_TOOL_PREFIX = "ultraTool_tool_"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.dataset.resolve()
    source = root / "derived"
    output = root / "prototype"
    export_manifest = json.loads((root / "analysis" / "exports.json").read_text(encoding="utf-8"))
    if digest(root / "SOURCE.json") != export_manifest["source_manifest_sha256"]:
        raise ValueError("Source manifest changed since the derived files were built")
    for item in export_manifest["files"]:
        path = root / item["path"]
        if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            raise ValueError(f"Derived input changed: {path}")

    all_tools = read_jsonl(source / "tools.jsonl")
    all_queries = read_jsonl(source / "queries.jsonl")
    all_qrels = read_jsonl(source / "qrels.jsonl")
    held_tools = [tool for tool in all_tools if tool["id"].startswith(ULTRATOOL_TOOL_PREFIX)]
    held_queries = [query for query in all_queries if query["source_dataset"] == ULTRATOOL_SOURCE]
    held_tool_ids = {tool["id"] for tool in held_tools}
    held_query_ids = {query["id"] for query in held_queries}
    if not held_tools or not held_queries:
        raise ValueError("Expected UltraTool data not found")
    if len(held_tool_ids) != len(held_tools) or len(held_query_ids) != len(held_queries):
        raise ValueError("Duplicate held-out IDs")
    held_label_ids = {str(label["id"]) for query in held_queries for label in query["labels"]}
    if not held_label_ids <= held_tool_ids:
        raise ValueError("UltraTool labels reference non-UltraTool tools; holdout policy needs review")
    other_label_ids = {str(label["id"]) for query in all_queries if query["id"] not in held_query_ids for label in query["labels"]}
    if other_label_ids & held_tool_ids:
        raise ValueError("Retained queries reference held-out tools; holdout policy needs review")

    tools = [tool for tool in all_tools if tool["id"] not in held_tool_ids]
    queries = [query for query in all_queries if query["id"] not in held_query_ids]
    query_ids = {query["id"] for query in queries}
    tool_ids = {tool["id"] for tool in tools}
    qrels = [row for row in all_qrels if row["query_id"] in query_ids]
    if len(query_ids) != len(queries) or len(tool_ids) != len(tools):
        raise ValueError("Duplicate retained IDs")
    if any(row["tool_id"] not in tool_ids for row in qrels):
        raise ValueError("Retained qrel references a held-out or missing tool")
    qrel_counts = Counter(row["query_id"] for row in qrels if int(row["relevance"]) > 0)
    if any(qrel_counts[qid] == 0 for qid in query_ids):
        raise ValueError("Retained query lacks a positive qrel")
    if len(qrels) + sum(row["query_id"] in held_query_ids for row in all_qrels) != len(all_qrels):
        raise ValueError("A qrel was neither retained nor held out")

    output.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, rows in (("tools", tools), ("queries", queries), ("qrels", qrels)):
        path = output / f"{name}.jsonl"
        write_jsonl(path, rows)
        written[name] = {"path": path.relative_to(root).as_posix(), "rows": len(rows), "sha256": digest(path)}
    metadata = {
        "purpose": "Prototype evaluation view with UltraTool queries and tools held out",
        "input_source_manifest_sha256": export_manifest["source_manifest_sha256"],
        "input_derived_sha256": {item["path"]: item["sha256"] for item in export_manifest["files"]},
        "holdout_rule": {"query_source_dataset": ULTRATOOL_SOURCE, "tool_id_prefix": ULTRATOOL_TOOL_PREFIX},
        "held_out": {"queries": len(held_queries), "tools": len(held_tools), "qrels": len(all_qrels) - len(qrels), "distinct_label_tool_ids": len(held_label_ids)},
        "retained": {"queries": len(queries), "tools": len(tools), "qrels": len(qrels), "source_tasks": len({q["source_dataset"] for q in queries})},
        "files": written,
    }
    (output / "VIEW.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"held_out": metadata["held_out"], "retained": metadata["retained"]}, indent=2))


if __name__ == "__main__":
    main()
