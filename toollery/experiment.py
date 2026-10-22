from __future__ import annotations

import time

from .data import Dataset
from .metrics import evaluate
from .retrieval import BM25Index, REPRESENTATIONS, ToolSearch, build_documents, tokenize
from .storage import digest


def run_experiment(dataset: Dataset, manuals: dict[str, dict] | None, *, k: int = 10, include_all_rankings: bool = False, max_queries: int | None = None, progress=None) -> dict:
    if k < 1 or (max_queries is not None and max_queries < 1):
        raise ValueError("k and max_queries must be positive")
    queries = dataset.queries[:max_queries] if max_queries else dataset.queries
    selected_ids = {query.query_id for query in queries}
    qrels = {query_id: dataset.qrels[query_id] for query_id in selected_ids}
    spec_lengths = {tool_id: len(tokenize(tool.spec_text)) for tool_id, tool in dataset.pool.tools.items()}
    full_spec_length = sum(spec_lengths.values())
    experiment = {
        "dataset": dataset.provenance,
        "pool": {"tool_count": len(dataset.pool.tools), "snapshot_hash": dataset.pool.snapshot_hash},
        "queries": {"count": len(queries), "ids_hash": digest([query.query_id for query in queries]), "selection": "first_n_in_file_order" if max_queries else "all", "empty_query_ids": [query.query_id for query in queries if not query.text.strip()]},
        "evaluation": {"k": k, "top_k_and_all_are_separate_budgets": True, "test_queries_used_only_for_retrieval": True, "qrels_used_only_for_scoring": True},
        "representations": {},
    }
    for representation in REPRESENTATIONS:
        if representation != "spec" and manuals is None:
            continue
        started = time.perf_counter()
        documents = build_documents(dataset.pool, representation, manuals)
        index = BM25Index(documents)
        search = ToolSearch(dataset.pool, index)
        top_runs, all_ranked_runs = {}, {}
        build_seconds = time.perf_counter() - started
        search_seconds = 0.0
        selected_tokens = 0
        for index_number, query in enumerate(queries, 1):
            search_started = time.perf_counter()
            top_hits = search.select(query.text, budget="top_k", k=k)
            search_seconds += time.perf_counter() - search_started
            top_runs[query.query_id] = top_hits
            top_tokens = sum(spec_lengths[hit.tool_id] for hit in top_hits)
            selected_tokens += top_tokens
            if include_all_rankings:
                # Only the ranking prefix is needed for @k. Complete zero-score
                # tails use the same deterministic ID tie-break as all-budget search.
                prefix = list(top_hits)
                selected = {hit.tool_id for hit in prefix}
                if len(prefix) < min(k, len(dataset.pool.tools)):
                    from .retrieval import RankedTool
                    for tool_id in dataset.pool.tool_ids:
                        if tool_id not in selected:
                            prefix.append(RankedTool(tool_id, 0.0))
                            if len(prefix) == min(k, len(dataset.pool.tools)):
                                break
                all_ranked_runs[query.query_id] = prefix
            if progress is not None and (index_number % 500 == 0 or index_number == len(queries)):
                progress(representation, index_number, len(queries))
        elapsed = time.perf_counter() - started
        result = {
            "index": index.metadata,
            "top_k": evaluate(top_runs, qrels, k=k),
            "average_selected_spec_tokens_proxy": selected_tokens / len(queries),
            "average_top_k_retrieval_ms": search_seconds * 1000 / len(queries),
            "index_build_seconds": build_seconds,
            "total_seconds": elapsed,
            "predictions": {query_id: [{"tool_id": hit.tool_id, "score": hit.score} for hit in hits] for query_id, hits in top_runs.items()},
            "by_source_dataset": {},
        }
        for source in sorted({query.source_dataset for query in queries}):
            source_runs = {query.query_id: top_runs[query.query_id] for query in queries if query.source_dataset == source}
            source_metrics = evaluate(source_runs, qrels, k=k)
            result["by_source_dataset"][source] = {key: value for key, value in source_metrics.items() if key != "per_query"}
        if include_all_rankings:
            result["all_budget_ranked"] = evaluate(all_ranked_runs, qrels, k=k)
            result["all_budget_ranked"]["exposed_tool_count"] = len(dataset.pool.tools)
            result["all_budget_ranked"]["note"] = "Ranking metrics use only the first k positions; all tools would be exposed. Zero-score tails are ordered by tool_id."
            result["average_all_spec_tokens_proxy"] = full_spec_length
        experiment["representations"][representation] = result
    full = {}
    for query in queries:
        relevant = {tool_id for tool_id, grade in dataset.qrels[query.query_id].items() if grade > 0}
        full[query.query_id] = {"recall": 1.0, "success": 1.0, "selected_tool_count": len(dataset.pool.tools), "relevant_tool_count": len(relevant)}
    experiment["full_library_exposure"] = {
        "recall": 1.0,
        "success": 1.0,
        "candidate_pool_size": len(dataset.pool.tools),
        "spec_token_count_proxy": full_spec_length,
        "average_relevant_tools_per_query": sum(row["relevant_tool_count"] for row in full.values()) / len(full),
        "ranking_metrics": None,
        "note": "No retrieval ordering is defined for this baseline; ranking metrics are intentionally omitted.",
        "token_count_note": "Counts use retrieval tokenizer, not an LLM tokenizer; these are size proxies, not billed token measurements.",
    }
    return experiment
