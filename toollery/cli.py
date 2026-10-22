from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys

from . import __version__
from .data import Dataset, ToolPool
from .evidence import normalize
from .experiment import run_experiment
from .generation import make_generator
from .manuals import ManualCache, PreparationError, prepare_manuals
from .retrieval import BM25Index, REPRESENTATIONS, ToolSearch, build_documents
from .storage import atomic_json, file_digest


def positive(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("Must be positive")
    return result


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Toollery offline prototype: tool-only manual generation and full-pool retrieval")
    subparsers = root.add_subparsers(dest="command", required=True)
    for name in ("inspect", "prepare", "evaluate", "search"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--dataset", type=Path, default=Path("benchmark/toolret/prototype"))
        sub.add_argument("--output", type=Path, default=Path("runs") / f"toollery-{name}.json")
        if name != "inspect":
            sub.add_argument("--cache", type=Path, default=Path("work/toollery-cache"))
            sub.add_argument("--generator-config", type=Path, help="Provider JSON; defaults to explicitly labelled mock generator")
            sub.add_argument("--proxy-count", type=positive, default=3)
            sub.add_argument("--evidence-limit", type=positive, default=24000)
        if name == "prepare":
            sub.add_argument("--max-new", type=positive, help="Maximum uncached tools attempted in this invocation; partial preparation exits 2")
            sub.add_argument("--attempts", type=positive, default=2)
        if name == "evaluate":
            sub.add_argument("--with-manuals", action="store_true", help="Evaluate all three representations; matching cache must already cover entire pool")
            sub.add_argument("--all-rankings", action="store_true", help="Also evaluate all-budget rankings; can use substantial memory")
            sub.add_argument("--max-queries", type=positive, help="Pilot query limit; NEVER reduces candidate pool")
            sub.add_argument("--k", type=positive, default=10)
        if name == "search":
            sub.add_argument("--query", required=True)
            sub.add_argument("--representation", choices=REPRESENTATIONS, default="spec")
            sub.add_argument("--budget", choices=("top_k", "all"), default="top_k")
            sub.add_argument("--k", type=positive, default=10)
    return root


def progress(*values):
    print("progress:", *values, file=sys.stderr, flush=True)


def metadata() -> dict:
    directory = Path(__file__).parent
    return {
        "toollery_version": __version__,
        "python": platform.python_version(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_hashes": {path.name: file_digest(path) for path in sorted(directory.glob("*.py"))},
    }


def execute(args) -> dict:
    if args.command in {"inspect", "evaluate"}:
        dataset = Dataset.load(args.dataset)
        pool = dataset.pool
    else:
        # Preparation and interactive search never load query/label files.
        pool = ToolPool.load(args.dataset / "tools.jsonl")
    if args.command == "inspect":
        modes, failed, truncated = Counter(), [], 0
        for tool_id in pool.tool_ids:
            try:
                evidence = normalize(pool.tools[tool_id])
                modes[evidence.source_mode] += 1
                truncated += bool(evidence.truncated_fields)
            except ValueError:
                failed.append(tool_id)
        return {
            "status": "inspected", "tool_count": len(pool.tools), "query_count": len(dataset.queries),
            "positive_qrel_count": sum(len(labels) for labels in dataset.qrels.values()),
            "snapshot_hash": pool.snapshot_hash, "evidence_source_modes": dict(modes),
            "truncated_tools": truncated, "unusable_tools": failed, "dataset": dataset.provenance,
            "empty_query_ids": [query.query_id for query in dataset.queries if not query.text.strip()],
        }
    config = json.loads(args.generator_config.read_text(encoding="utf-8-sig")) if args.generator_config else {"provider": "mock"}
    generator = make_generator(config)
    prepared = None
    needs_manuals = args.command == "prepare" or (args.command == "evaluate" and args.with_manuals) or (args.command == "search" and args.representation != "spec")
    if needs_manuals:
        prepared = prepare_manuals(
            pool, generator, ManualCache(args.cache), count=args.proxy_count,
            evidence_limit=args.evidence_limit, attempts=getattr(args, "attempts", 2),
            cache_only=args.command != "prepare", max_new=getattr(args, "max_new", None), progress=progress,
        )
    if args.command == "prepare":
        return {"status": "ready", "preparation": prepared.report, "mock": generator.contract["mock"]}
    if args.command == "evaluate":
        result = run_experiment(dataset, prepared.records if prepared else None, k=args.k, include_all_rankings=args.all_rankings, max_queries=args.max_queries, progress=progress)
        result.update({"status": "complete", "preparation": prepared.report if prepared else None, "mock_manuals": bool(prepared and generator.contract["mock"])})
        if result["mock_manuals"]:
            result["warning"] = "MOCK MANUALS: pipeline verification only. These results are not evidence of LLM/manual retrieval quality."
        return result
    documents = build_documents(pool, args.representation, prepared.records if prepared else None)
    index = BM25Index(documents)
    service = ToolSearch(pool, index)
    hits = service.select(args.query, budget=args.budget, k=args.k)
    return {
        "status": "complete", "snapshot_hash": pool.snapshot_hash, "pool_size": len(pool.tools),
        "representation": args.representation, "budget": args.budget, "k": args.k,
        "index": index.metadata, "mock_manuals": bool(prepared and generator.contract["mock"]),
        "selected": [{"tool_id": hit.tool_id, "score": hit.score, "documentation": pool.tools[hit.tool_id].documentation} for hit in hits],
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    exit_code = 0
    try:
        result = execute(args)
    except PreparationError as error:
        result = {"status": "not_ready", "preparation": error.report}
        exit_code = 2
    except Exception as error:
        # Do not reflect provider stdout, stderr, or arbitrary credentials into logs.
        result = {"status": "failed", "error_type": type(error).__name__}
        print(f"Failed: {type(error).__name__}", file=sys.stderr)
        exit_code = 1
    result["run"] = metadata()
    atomic_json(args.output, result)
    print(json.dumps({"status": result["status"], "output": str(args.output.resolve())}, ensure_ascii=False))
    return exit_code
