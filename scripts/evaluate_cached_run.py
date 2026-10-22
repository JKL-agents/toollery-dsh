"""Evaluate the successfully cached manuals from a partially covered run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from toollery.data import Dataset, ToolPool
from toollery.experiment import run_experiment
from toollery_dsh.configuration import resolve_generator
from toollery.generation import make_generator
from toollery.manuals import ManualCache, prepare_manuals
from toollery.storage import atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", default="benchmark/toolret/development")
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--run-prefix", required=True)
    args = parser.parse_args()
    source = ROOT / args.dataset_dir
    full = Dataset.load(source)
    records = {}
    for path in (ROOT / args.cache_dir / "manuals").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        records[record["tool_id"]] = record
    ids = [tool_id for tool_id in full.pool.tool_ids if tool_id in records]
    pool = ToolPool([full.pool.tools[tool_id] for tool_id in ids])
    selected = set(ids)
    excluded = sorted(set(full.pool.tool_ids) - selected)
    queries = [q for q in full.queries if set(full.qrels[q.query_id]).issubset(selected)]
    qrels = {q.query_id: full.qrels[q.query_id] for q in queries}
    dataset = Dataset(pool, queries, qrels, {**full.provenance, "effective_cached_tool_count": len(ids), "excluded_tool_count": len(full.pool.tools) - len(ids)})
    config = json.loads((ROOT / "examples/generator.command.json").read_text(encoding="utf-8-sig"))
    prepared = prepare_manuals(pool, make_generator(resolve_generator(config)), ManualCache(ROOT / args.cache_dir), count=3, cache_only=True)
    print(json.dumps({"validated_manual_tools": len(prepared.records), "queries": len(queries), "excluded_tool_ids": excluded}), flush=True)
    result = run_experiment(dataset, prepared.records, k=10,
        progress=lambda mode, done, total: print(f"evaluation: {mode} {done}/{total}", flush=True))
    result["scope"] = f"{len(ids)} tools with accepted cached manuals from the {len(full.pool.tools)}-tool development subset; {len(full.pool.tools)-len(ids)} tools excluded after provider failures"
    result["generation_model"] = config["model"]
    result["proxy_query_count"] = 3
    result["generation_coverage"] = {"accepted_manual_tools": len(ids), "source_tools": len(full.pool.tools), "excluded_tools": len(excluded), "excluded_tool_ids": excluded, "excluded_query_ids": [q.query_id for q in full.queries if q.query_id not in qrels], "cache_dir": args.cache_dir}
    atomic_json(ROOT / f"runs/{args.run_prefix}-evaluation.json", result)
    atomic_json(ROOT / f"runs/{args.run_prefix}-generation.json", {
        "selection": {"tool_ids": ids, "tool_count": len(ids), "snapshot_hash": pool.snapshot_hash},
        "scope": result["scope"], "generation_coverage": result["generation_coverage"],
        "cache_only_validation": prepared.report,
        "generation_pass_reports": ["toollery-development-10661-full-generation.json", "toollery-development-10661-full-rerun-generation.json", "toollery-development-10661-full-rerun2-generation.json"],
    })
    print(json.dumps({"tool_count": len(ids), "query_count": len(queries), "excluded_tools": len(full.pool.tools)-len(ids), "metrics": {name: rep["top_k"]["macro_average"] for name, rep in result["representations"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
