"""Verify a local ToolRet snapshot and export inspection-friendly JSONL files.

Uses only local data, Python's standard library and pyarrow. No tool execution,
manual generation, relabeling, filtering or benchmark scoring takes place.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, values: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def decoded(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def lengths(values: list[int]) -> dict:
    values = sorted(values)
    return {"min": min(values), "median": statistics.median(values),
            "mean": round(statistics.mean(values), 2),
            "p95": values[int((len(values) - 1) * .95)], "max": max(values)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "benchmark" / "toolret")
    args = parser.parse_args()
    root = args.dataset.resolve()
    manifest = json.loads((root / "SOURCE.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        content = (root / item["path"]).read_bytes()
        if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"Snapshot checksum mismatch: {item['path']}")

    tools, queries = [], []
    schemas, task_stats = {}, {}
    expected_counts = {}
    for dataset in manifest["datasets"]:
        metadata = json.loads((root / dataset["metadata"]).read_text(encoding="utf-8"))
        for config in metadata["cardData"]["dataset_info"]:
            for split in config["splits"]:
                expected_counts[(dataset["repository"], config["config_name"])] = split["num_examples"]
    for family, split in (("ToolRet-Tools", "tools"), ("ToolRet-Queries", "queries")):
        counts = Counter()
        for path in sorted((root / "raw" / family).rglob("*.parquet")):
            config = path.parent.name
            table = pq.read_table(path)
            schema = {field.name: str(field.type) for field in table.schema}
            if family in schemas and schemas[family] != schema:
                raise ValueError(f"Inconsistent schema in {path}")
            schemas[family] = schema
            rows = table.to_pylist()
            counts[config] += len(rows)
            for row in rows:
                if split == "tools":
                    row["category"] = config
                    tools.append(row)
                else:
                    row["source_dataset"] = config
                    row["labels"] = json.loads(row["labels"])
                    queries.append(row)
        for config, count in counts.items():
            if count != expected_counts[(f"mangopy/{family}", config)]:
                raise ValueError(f"Row count differs from dataset card: {family}/{config}")

    tool_counts = Counter(t["id"] for t in tools)
    query_counts = Counter(q["id"] for q in queries)
    if any(n != 1 for n in tool_counts.values()) or any(n != 1 for n in query_counts.values()):
        raise ValueError("IDs are not unique; exports would require an explicit identity policy")
    by_id = {t["id"]: t for t in tools}
    parsed_docs = {t["id"]: decoded(t["documentation"]) for t in tools}
    tool_stats = {}
    names = defaultdict(list)
    exact_docs = defaultdict(list)
    canonical_docs = defaultdict(list)
    missing_description_ids = []
    for category in sorted({t["category"] for t in tools}):
        subset = [t for t in tools if t["category"] == category]
        shapes, fields, description_types, parameter_types = Counter(), Counter(), Counter(), Counter()
        no_description = []
        for tool in subset:
            doc = parsed_docs[tool["id"]]
            shapes[type(doc).__name__] += 1
            if isinstance(doc, dict):
                fields.update(doc.keys())
                description_types[type(doc.get("description")).__name__] += 1
                parameter_types[type(doc.get("parameters")).__name__] += 1
                if not isinstance(doc.get("description"), str) or not doc["description"].strip():
                    no_description.append(tool["id"])
                if doc.get("name"):
                    names[canonical(doc["name"])].append(tool["id"])
            exact_docs[tool["documentation"]].append(tool["id"])
            canonical_docs[canonical(doc)].append(tool["id"])
        missing_description_ids.extend(no_description)
        tool_stats[category] = {
            "count": len(subset), "decoded_documentation_types": dict(shapes),
            "top_level_fields": dict(fields.most_common()),
            "description_value_types": dict(description_types),
            "parameters_value_types": dict(parameter_types),
            "missing_or_empty_top_level_description": len(no_description),
            "empty_documentation": sum(not (t["documentation"] or "").strip() for t in subset),
            "documentation_length_characters": lengths([len(t["documentation"] or "") for t in subset]),
        }

    label_keys, relevance_values, labels_per_query = Counter(), Counter(), Counter()
    missing_refs, doc_mismatches, category_mismatches = [], [], []
    mismatch_patterns, mismatch_sources, mismatch_name_conflicts = Counter(), Counter(), Counter()
    duplicate_labels, no_positive_queries = [], []
    referenced_ids, qrels = set(), []
    for query in queries:
        labels = query["labels"]
        if not isinstance(labels, list):
            raise ValueError(f"Labels are not a list: {query['id']}")
        task = query["source_dataset"]
        stat = task_stats.setdefault(task, {"queries": 0, "categories": Counter(), "label_entries": 0,
                                            "positive_pairs": 0, "single_tool_queries": 0,
                                            "multi_tool_queries": 0, "unique_target_ids": set()})
        stat["queries"] += 1
        stat["categories"][query["category"]] += 1
        stat["label_entries"] += len(labels)
        labels_per_query[len(labels)] += 1
        ids = Counter(str(label["id"]) for label in labels)
        if any(n > 1 for n in ids.values()):
            duplicate_labels.append({"query_id": query["id"], "ids": dict(ids)})
        positives = set()
        # The official evaluator creates a dict from labels; repeated IDs use the last value.
        qrel_map = {}
        for label in labels:
            label_keys.update(label.keys())
            tid = str(label["id"])
            rel = int(label["relevance"])
            relevance_values[str(label["relevance"])] += 1
            qrel_map[tid] = rel
            referenced_ids.add(tid)
            if tid not in by_id:
                missing_refs.append({"query_id": query["id"], "tool_id": tid})
            else:
                if query["category"] != by_id[tid]["category"]:
                    category_mismatches.append({"query_id": query["id"], "tool_id": tid})
                if "doc" in label and decoded(label["doc"]) != parsed_docs[tid]:
                    label_doc, tool_doc = decoded(label["doc"]), parsed_docs[tid]
                    if isinstance(label_doc, dict) and isinstance(tool_doc, dict):
                        if {k: v for k, v in label_doc.items() if k != "id"} == tool_doc:
                            pattern = "auxiliary_label_id_only"
                        elif set(label_doc) == set(tool_doc):
                            pattern = "same_keys_changed_values"
                        else:
                            pattern = "different_key_sets"
                        if label_doc.get("name") != tool_doc.get("name"):
                            mismatch_name_conflicts[task] += 1
                    else:
                        pattern = "different_types"
                    mismatch_patterns[pattern] += 1
                    mismatch_sources[task] += 1
                    doc_mismatches.append({"query_id": query["id"], "tool_id": tid,
                                           "source_dataset": task, "pattern": pattern})
        for tid, rel in qrel_map.items():
            qrels.append({"query_id": query["id"], "tool_id": tid, "relevance": rel})
            if rel > 0:
                positives.add(tid)
        stat["positive_pairs"] += len(positives)
        stat["unique_target_ids"].update(positives)
        if len(positives) == 1:
            stat["single_tool_queries"] += 1
        elif len(positives) > 1:
            stat["multi_tool_queries"] += 1
        else:
            no_positive_queries.append(query["id"])

    for stat in task_stats.values():
        stat["categories"] = dict(stat["categories"])
        stat["unique_target_ids"] = len(stat["unique_target_ids"])
    by_category = {}
    for category in tool_stats:
        subset = [q for q in queries if q["category"] == category]
        ids = {q["id"] for q in subset}
        by_category[category] = {
            "tools": tool_stats[category]["count"], "queries": len(subset),
            "query_configs": len({q["source_dataset"] for q in subset}),
            "qrels": sum(qr["query_id"] in ids for qr in qrels),
            "single_tool_queries": sum(len({l['id'] for l in q['labels'] if int(l['relevance']) > 0}) == 1 for q in subset),
            "multi_tool_queries": sum(len({l['id'] for l in q['labels'] if int(l['relevance']) > 0}) > 1 for q in subset),
        }
    duplicate_names = [ids for ids in names.values() if len(ids) > 1]
    duplicate_docs = [ids for ids in exact_docs.values() if len(ids) > 1]
    duplicate_canonical_docs = [ids for ids in canonical_docs.values() if len(ids) > 1]
    query_texts = Counter(q["query"] for q in queries)
    cross_category_docs = [ids for ids in duplicate_canonical_docs if len({by_id[tid]["category"] for tid in ids}) > 1]
    stats = {
        "tool_count": len(tools), "query_count": len(queries), "query_config_count": len(task_stats),
        "label_entries": sum(labels_per_query_count * n for n, labels_per_query_count in labels_per_query.items()),
        "qrel_pairs": len(qrels), "schemas": schemas, "by_category": by_category,
        "tools": tool_stats, "query_configs": task_stats,
        "labels_per_query_distribution": dict(sorted(labels_per_query.items())),
        "label_fields_occurrences": dict(label_keys), "relevance_values": dict(relevance_values),
        "queries_with_empty_query": sum(not (q["query"] or "").strip() for q in queries),
        "queries_with_empty_instruction": sum(not (q["instruction"] or "").strip() for q in queries),
        "query_length_characters": lengths([len(q["query"] or "") for q in queries]),
        "instruction_length_characters": lengths([len(q["instruction"] or "") for q in queries]),
        "unique_referenced_tools": len(referenced_ids),
        "tools_not_referenced_by_labels": len(set(by_id) - referenced_ids),
        "integrity": {"verified_source_files": len(manifest["files"]), "unique_tool_ids": len(tool_counts),
                      "unique_query_ids": len(query_counts), "missing_label_references": len(missing_refs),
                      "label_doc_mismatches": len(doc_mismatches), "label_category_mismatches": len(category_mismatches),
                      "queries_with_repeated_label_ids": len(duplicate_labels),
                      "queries_without_positive_labels": len(no_positive_queries)},
        "label_doc_mismatch_patterns": dict(mismatch_patterns),
        "label_doc_mismatches_by_source": dict(mismatch_sources),
        "label_doc_name_conflicts_by_source": dict(mismatch_name_conflicts),
        "exact_documentation_duplicate_groups": len(duplicate_docs),
        "exact_documentation_duplicate_extra_rows": sum(len(ids) - 1 for ids in duplicate_docs),
        "canonical_documentation_duplicate_groups": len(duplicate_canonical_docs),
        "canonical_documentation_duplicate_extra_rows": sum(len(ids) - 1 for ids in duplicate_canonical_docs),
        "canonical_documentation_duplicate_cross_category_groups": len(cross_category_docs),
        "repeated_name_groups": len(duplicate_names), "tools_in_repeated_name_groups": sum(map(len, duplicate_names)),
        "exact_query_text_duplicate_groups": sum(n > 1 for n in query_texts.values()),
        "exact_query_text_duplicate_extra_rows": sum(n - 1 for n in query_texts.values() if n > 1),
    }
    derived = root / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    write_jsonl(derived / "tools.jsonl", tools)
    write_jsonl(derived / "queries.jsonl", queries)
    write_jsonl(derived / "qrels.jsonl", qrels)
    write_json(root / "analysis" / "statistics.json", stats)
    write_json(root / "analysis" / "integrity-findings.json", {
        "missing_label_references": missing_refs, "label_doc_mismatches": doc_mismatches,
        "label_category_mismatches": category_mismatches, "repeated_label_ids": duplicate_labels,
        "queries_without_positive_labels": no_positive_queries,
        "missing_or_empty_top_level_description_ids": missing_description_ids,
        "exact_documentation_duplicate_groups": duplicate_docs,
        "canonical_documentation_duplicate_groups": duplicate_canonical_docs,
        "repeated_name_groups_examples": duplicate_names[:20],
    })
    samples = {}
    for category in tool_stats:
        query = next(q for q in queries if q["category"] == category and all(l['id'] in by_id for l in q['labels']))
        samples[category] = {"query": query,
                             "tools": [{**by_id[l["id"]], "documentation_decoded": parsed_docs[l["id"]]}
                                       for l in query["labels"]]}
    # Short, actual single-tool example suitable for a human-facing structure explanation.
    simple = min((q for q in queries if len(q["labels"]) == 1 and q["labels"][0]["id"] in by_id
                  and isinstance(parsed_docs[q["labels"][0]["id"]], dict)
                  and isinstance(parsed_docs[q["labels"][0]["id"]].get("parameters"), dict)),
                 key=lambda q: len(q["query"]) + len(by_id[q["labels"][0]["id"]]["documentation"]))
    samples["simple_single_tool"] = {"query": simple,
                                     "tool": by_id[simple["labels"][0]["id"]],
                                     "documentation_decoded": parsed_docs[simple["labels"][0]["id"]]}
    write_json(root / "analysis" / "samples.json", samples)
    exports = [{"path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(derived.glob("*.jsonl"))]
    write_json(root / "analysis" / "exports.json", {"source_manifest_sha256": hashlib.sha256((root / "SOURCE.json").read_bytes()).hexdigest(),
                                                   "files": exports})
    print(json.dumps({key: stats[key] for key in ("tool_count", "query_count", "query_config_count", "qrel_pairs", "by_category", "integrity")}, indent=2))


if __name__ == "__main__":
    main()
