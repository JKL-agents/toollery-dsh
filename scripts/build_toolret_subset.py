"""Build a deterministic, cost-controlled ToolRet development subset.

The source prototype view is never modified. All non-web tools are retained;
web tools are selected by first covering a cross-source set of web queries with
their complete positive tool sets, then adding deterministic web distractors.
Only queries whose positive labels are fully contained in the frozen pool are
retained.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmark/toolret/prototype"
OUTPUT = ROOT / "benchmark/toolret/development"
SEED = 31
WEB_TOOL_TARGET = 3500
WEB_RELEVANT_TARGET = 2600


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> str:
    text = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def order(value: str) -> str:
    return hashlib.sha256(f"{SEED}:{value}".encode("utf-8")).hexdigest()


def main() -> int:
    tools = read_jsonl(SOURCE / "tools.jsonl")
    queries = read_jsonl(SOURCE / "queries.jsonl")
    qrels = read_jsonl(SOURCE / "qrels.jsonl")
    category = {row["id"]: row.get("category", "unknown") for row in tools}
    positives: dict[str, set[str]] = defaultdict(set)
    for row in qrels:
        if float(row["relevance"]) > 0:
            positives[row["query_id"]].add(row["tool_id"])

    if len(set(category.values()) - {"code", "customized", "web"}) > 0:
        raise ValueError("Unexpected tool category")
    if WEB_RELEVANT_TARGET >= WEB_TOOL_TARGET:
        raise ValueError("Relevant target must be smaller than web target")

    web_queries: dict[str, list[dict]] = defaultdict(list)
    for query in queries:
        ids = positives[query["id"]]
        if ids and {category[tool_id] for tool_id in ids} == {"web"}:
            web_queries[str(query.get("source_dataset", "unknown"))].append(query)
    for source in web_queries:
        web_queries[source].sort(key=lambda row: order(row["id"]))

    selected_web_relevant: set[str] = set()
    selected_web_query_ids: set[str] = set()
    cursors = {source: 0 for source in web_queries}
    # Round-robin source tasks so a large source cannot consume the budget.
    while True:
        changed = False
        for source in sorted(web_queries):
            cursor = cursors[source]
            if cursor >= len(web_queries[source]):
                continue
            query = web_queries[source][cursor]
            cursors[source] += 1
            new_ids = positives[query["id"]] - selected_web_relevant
            if len(selected_web_relevant | new_ids) <= WEB_RELEVANT_TARGET:
                selected_web_query_ids.add(query["id"])
                selected_web_relevant.update(new_ids)
                changed = True
        if not changed:
            break

    all_web = {tool_id for tool_id, value in category.items() if value == "web"}
    web_noise = sorted(all_web - selected_web_relevant, key=order)
    selected_web_noise = set(web_noise[: WEB_TOOL_TARGET - len(selected_web_relevant)])
    selected_tool_ids = {
        tool_id for tool_id, value in category.items() if value in {"code", "customized"}
    } | selected_web_relevant | selected_web_noise

    eligible_queries = [
        query for query in queries
        if positives[query["id"]] and positives[query["id"]].issubset(selected_tool_ids)
    ]
    eligible_ids = {query["id"] for query in eligible_queries}
    retained_qrels = [row for row in qrels if row["query_id"] in eligible_ids]
    retained_tools = [row for row in tools if row["id"] in selected_tool_ids]
    if len(retained_tools) != len(selected_tool_ids):
        raise AssertionError("Tool selection contains missing IDs")
    if any(row["tool_id"] not in selected_tool_ids for row in retained_qrels):
        raise AssertionError("Retained qrels contain a dangling tool")
    if any(not positives[query["id"]].issubset(selected_tool_ids) for query in eligible_queries):
        raise AssertionError("Query eligibility invariant failed")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    hashes = {
        "tools": write_jsonl(OUTPUT / "tools.jsonl", retained_tools),
        "queries": write_jsonl(OUTPUT / "queries.jsonl", eligible_queries),
        "qrels": write_jsonl(OUTPUT / "qrels.jsonl", retained_qrels),
    }
    source_counts = Counter(row.get("source_dataset", "unknown") for row in eligible_queries)
    category_counts = Counter(category[row["id"]] for row in retained_tools)
    selected_relevant_by_category = Counter(category[tool_id] for tool_id in selected_web_relevant)
    manifest = {
        "purpose": "Cost-controlled ToolRet development subset; source prototype is unchanged",
        "source_view": str(SOURCE.relative_to(ROOT)).replace("\\", "/"),
        "seed": SEED,
        "selection": {
            "retain_all_categories": ["code", "customized"],
            "web_tool_target": WEB_TOOL_TARGET,
            "web_relevant_target": WEB_RELEVANT_TARGET,
            "web_noise_target": WEB_TOOL_TARGET - WEB_RELEVANT_TARGET,
            "web_relevant_rule": "round-robin by source_dataset over web-only queries; retain complete positive tool set",
            "web_noise_rule": "SHA256(seed + tool_id) order over remaining web tools",
            "query_rule": "retain every original query with at least one positive qrel and all positive tools in frozen pool",
            "qrels_rule": "copy original positive relations without rewriting relevance",
        },
        "retained": {
            "tools": len(retained_tools),
            "queries": len(eligible_queries),
            "qrels": len(retained_qrels),
            "source_tasks": len(source_counts),
            "category_counts": dict(category_counts),
            "source_query_counts": dict(sorted(source_counts.items())),
            "positive_tool_counts_by_category": {
                "code": len({tool_id for tool_id in selected_tool_ids if category[tool_id] == "code" and tool_id in {t for ids in positives.values() for t in ids}}),
                "customized": len({tool_id for tool_id in selected_tool_ids if category[tool_id] == "customized" and tool_id in {t for ids in positives.values() for t in ids}}),
                "web": len(selected_web_relevant),
            },
            "web_relevant_tools": len(selected_web_relevant),
            "web_noise_tools": len(selected_web_noise),
            "selected_web_query_seed_count": len(selected_web_query_ids),
            "selected_web_seed_sources": len(web_queries),
        },
        "source_counts": {
            "tools": len(tools), "queries": len(queries), "qrels": len(qrels),
            "category_counts": dict(Counter(category.values())),
        },
        "hashes_sha256": hashes,
        "warning": "Development subset; results are not directly comparable with full ToolRet or ToolRet minus UltraTool.",
    }
    (OUTPUT / "VIEW.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
