"""Tool-only stratified generation pilot; benchmark queries are read afterwards."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from toollery.data import Dataset, ToolPool
from toollery.evidence import normalize
from toollery.experiment import run_experiment
from toollery_dsh.configuration import resolve_generator, load_key
from toollery.generation import make_generator
from toollery.manuals import ManualCache, PreparationError, prepare_manuals
from toollery.storage import atomic_json, read_jsonl


# API generation is I/O-bound. Use the project's chosen eight-worker baseline.
MAX_WORKERS = 8
DEFAULT_WORKERS = 8


def select_tools(pool, count=100, seed=31):
    # Round-robin across source families, and across evidence modes within each
    # family. Hash ordering is deterministic and never uses queries or qrels.
    groups = defaultdict(lambda: defaultdict(list))
    order = lambda value: hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()
    for tool_id in pool.tool_ids:
        tool = pool.tools[tool_id]
        source = tool_id.rsplit("_tool_", 1)[0]
        groups[source][normalize(tool).source_mode].append(tool_id)
    queues = {}
    for source, modes in groups.items():
        for ids in modes.values():
            ids.sort(key=order)
        queue = []
        while any(modes.values()):
            for mode in sorted(modes):
                if modes[mode]:
                    queue.append(modes[mode].pop(0))
        queues[source] = queue
    selected = []
    while len(selected) < min(count, len(pool.tools)):
        for source in sorted(queues, key=order):
            if queues[source] and len(selected) < count:
                selected.append(queues[source].pop(0))
    return selected


def write_view(dataset_dir, output, selected):
    output.mkdir(parents=True, exist_ok=True)
    selected = set(selected)
    rows = [r for r in read_jsonl(dataset_dir / "tools.jsonl") if r["id"] in selected]
    (output / "tools.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return rows


def eligible_queries(dataset, selected):
    selected = set(selected)
    return [q for q in dataset.queries if set(dataset.qrels[q.query_id]).issubset(selected)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--select-only", action="store_true")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--cache-dir", default="work/toollery-cache")
    parser.add_argument("--run-prefix", default="toollery-pilot-100")
    parser.add_argument("--dataset-dir", default="benchmark/toolret/development")
    parser.add_argument("--tool-count", type=int, default=None, help="Optional deterministic pilot size; omit to use the full dataset view")
    args = parser.parse_args()
    if not 1 <= args.workers <= MAX_WORKERS:
        parser.error(f"workers must be 1..{MAX_WORKERS}")
    os.chdir(ROOT)
    source = ROOT / args.dataset_dir
    if not (source / "tools.jsonl").exists():
        raise SystemExit(f"Dataset view not found: {source}. Run scripts/build_toolret_subset.py first.")
    if args.tool_count is not None and args.tool_count < 1:
        parser.error("tool-count must be positive")
    output = ROOT / "work" / f"{args.run_prefix}-view"
    full_pool = ToolPool.load(source / "tools.jsonl")
    ids = select_tools(full_pool, count=args.tool_count) if args.tool_count is not None else list(full_pool.tool_ids)
    rows = write_view(source, output, ids)
    pool = ToolPool.load(output / "tools.jsonl")
    manifest = {
        "selection": "full dataset view" if args.tool_count is None else "tool-only source/evidence-mode round robin with SHA256 ordering",
        "seed": 31, "tool_ids": ids, "tool_count": len(ids),
        "full_pool_count": len(full_pool.tools), "source_snapshot_hash": full_pool.snapshot_hash,
        "snapshot_hash": pool.snapshot_hash, "proxy_query_count": 3,
        "source_counts": dict(Counter(x.rsplit("_tool_", 1)[0] for x in ids)),
        "category_counts": dict(Counter(r.get("category", "unknown") for r in rows)),
        "evidence_modes": dict(Counter(normalize(t).source_mode for t in pool.tools.values())),
        "truncated_evidence_count": sum(bool(normalize(t).truncated_fields) for t in pool.tools.values()),
        "warning": "Development pilot; balanced sampling and reduced pool are not the full ToolRet benchmark.",
    }
    atomic_json(output / "VIEW.json", manifest)
    print(json.dumps({"view": str(output), "tool_count": len(ids), "full_pool_count": len(full_pool.tools), "proxy_query_count": 3}, ensure_ascii=False), flush=True)
    if args.select_only:
        return 0
    config = json.loads((ROOT / "examples/generator.command.json").read_text(encoding="utf-8-sig"))
    load_key(config.get("parameters", {}).get("api_key_env", "TOOLLERY_API_KEY"), ROOT)
    generator = make_generator(resolve_generator(config))
    cache = ManualCache(ROOT / args.cache_dir)
    run_prefix = args.run_prefix
    start = time.perf_counter()
    reports = []

    def generate(tool_id):
        try:
            result = prepare_manuals(ToolPool([pool.tools[tool_id]]), generator, cache, count=3, attempts=1)
            return result.report
        except PreparationError as error:
            return error.report

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        pending = {executor.submit(generate, tool_id): tool_id for tool_id in ids}
        for future in as_completed(pending):
            report = future.result()
            reports.append(report)
            if len(reports) % 100 == 0 or report["failures"]:
                print(json.dumps({"completed": len(reports), "total": len(ids), "tool_id": pending[future], "valid": report["valid"], "generated": report["generated"], "failures": len(report["failures"]) }), flush=True)
    totals = Counter()
    for report in reports:
        totals.update(report["usage_from_new_generations"])
    summary = {"selection": manifest, "elapsed_seconds": time.perf_counter() - start,
               "workers": args.workers, "max_workers": MAX_WORKERS,
               "usage_from_new_generations": dict(totals),
               "generated": sum(r["generated"] for r in reports), "cache_hits": sum(r["cache_hits"] for r in reports),
               "failures": [f for r in reports for f in r["failures"]], "tools": reports}
    atomic_json(ROOT / f"runs/{run_prefix}-generation.json", summary)
    if summary["failures"]:
        return 2
    prepared = prepare_manuals(pool, generator, cache, count=3, cache_only=True)
    # Labels are consulted only after tool selection and generation are frozen.
    full = Dataset.load(source)
    queries = eligible_queries(full, ids)
    summary["eligible_query_count"] = len(queries)
    atomic_json(ROOT / f"runs/{run_prefix}-generation.json", summary)
    review = ["# Three-query manual pilot", "", "Model: " + generator.contract["model"], ""]
    for tool_id in pool.tool_ids:
        review.extend(["## " + tool_id + " — " + pool.tools[tool_id].name, "", "Original documentation:", "", "```json", json.dumps(pool.tools[tool_id].documentation, ensure_ascii=False, indent=2), "```", ""])
        for i, entry in enumerate(prepared.records[tool_id]["manual"]["entries"], 1):
            review.extend([f"{i}. {entry['query']}", f"   - Scenario: {entry['scenario_type']}", f"   - Notes: {entry['generation_notes']}"])
        review.append("")
    (ROOT / f"runs/{run_prefix}-manuals.md").write_text("\n".join(review), encoding="utf-8")
    if not queries:
        print("No fully covered original queries; generation review exported, retrieval evaluation skipped.")
        return 0
    pilot = Dataset(pool, queries, {q.query_id: full.qrels[q.query_id] for q in queries},
                    {"source": full.provenance, "pilot_view": manifest, "query_selection": "all positive tools contained in frozen pilot pool; original qrels retained"})
    result = run_experiment(pilot, prepared.records, k=10)
    result["scope"] = (
        f"{len(ids)}-tool development subset; "
        "not comparable to the full ToolRet pool"
    )
    result["generation_model"] = generator.contract["model"]
    result["proxy_query_count"] = 3
    atomic_json(ROOT / f"runs/{run_prefix}-evaluation.json", result)
    print(json.dumps({"query_count": len(queries), "metrics": {name: r["top_k"]["macro_average"] for name, r in result["representations"].items()}}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
