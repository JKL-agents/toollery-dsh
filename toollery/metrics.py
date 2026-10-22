from __future__ import annotations

import math

from .retrieval import RankedTool


def evaluate(runs: dict[str, list[RankedTool]], qrels: dict[str, dict[str, float]], *, k: int = 10) -> dict:
    if k < 1 or not runs:
        raise ValueError("k and run count must be positive")
    summary = {}
    for name, results in runs.items():
        labels = qrels[name]
        selected = [hit.tool_id for hit in results]
        relevant = {tool_id for tool_id, grade in labels.items() if grade > 0}
        intersection = relevant.intersection(selected[:k])
        dcg = sum((2 ** labels.get(tool_id, 0.0) - 1) / math.log2(rank + 2) for rank, tool_id in enumerate(selected[:k]))
        ideal = sorted(labels.values(), reverse=True)[:k]
        idcg = sum((2 ** grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(ideal))
        summary[name] = {
            f"recall@{k}": len(intersection) / len(relevant),
            f"success@{k}": float(bool(intersection)),
            f"ndcg@{k}": dcg / idcg if idcg else 0.0,
            f"mrr@{k}": next((1.0 / (index + 1) for index, tool_id in enumerate(selected[:k]) if labels.get(tool_id, 0) > 0), 0.0),
            "returned_tools": len(selected),
        }
    count = len(summary)
    return {"query_count": count, "k": k, "macro_average": {metric: sum(row[metric] for row in summary.values()) / count for metric in next(iter(summary.values())) if metric != "returned_tools"}, "per_query": summary}
