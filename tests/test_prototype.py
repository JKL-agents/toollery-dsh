from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

from toollery.cli import main
from toollery.data import Dataset, ToolPool, ToolRecord
from toollery.evidence import normalize
from toollery.generation import CommandGenerator, GenerationResult, MockGenerator, SYSTEM_PROMPT, PROMPT_VERSION
from toollery.manuals import ManualCache, PreparationError, cache_identity, prepare_manuals
from toollery.metrics import evaluate
from toollery.retrieval import BM25Index, RankedTool, ToolSearch, build_documents, tokenize
from toollery.storage import digest


class CountingGenerator(MockGenerator):
    def __init__(self):
        self.calls = 0

    def generate(self, evidence, count):
        self.calls += 1
        return super().generate(evidence, count)


def sample_pool():
    return ToolPool([
        ToolRecord("weather", {"name": "forecast", "description": "Check weather forecasts for a city", "parameters": {"city": "string"}}),
        ToolRecord("calculator", {"name": "sum", "description": "Add two numbers", "parameters": ["a", "b"]}),
        ToolRecord("calendar", {"name": "events", "functionality": "Find calendar appointments", "method": "GET"}),
    ])


class PrototypeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.pool = sample_pool()

    def tearDown(self):
        self.temporary.cleanup()

    def test_cache_reuse_single_tool_change_and_corruption(self):
        generator, cache = CountingGenerator(), ManualCache(self.root)
        first = prepare_manuals(self.pool, generator, cache, count=2)
        self.assertEqual(generator.calls, 3)
        second = prepare_manuals(self.pool, generator, cache, count=2)
        self.assertEqual(generator.calls, 3)
        self.assertEqual(second.report["cache_hits"], 3)
        changed = ToolPool([ToolRecord(tool.tool_id, {**tool.documentation, "description": "New calendar event creation"}) if tool.tool_id == "calendar" else tool for tool in self.pool.tools.values()])
        prepare_manuals(changed, generator, cache, count=2)
        self.assertEqual(generator.calls, 4)
        weather_record = first.records["weather"]
        cache.path(weather_record["manual_key"]).write_text('{"truncated":', encoding="utf-8")
        with self.assertRaises(PreparationError) as raised:
            prepare_manuals(self.pool, generator, cache, count=2, cache_only=True)
        self.assertEqual(raised.exception.report["valid"], 2)
        prepare_manuals(self.pool, generator, cache, count=2)
        self.assertEqual(generator.calls, 5)

    def test_evidence_is_deterministic_and_preserves_missing_description(self):
        first = ToolRecord("x", {"parameters": ["a", "b"], "functionality": "Add", "name": "sum", "instruction": "LEAK"})
        second = ToolRecord("x", {"name": "sum", "instruction": "CHANGED", "functionality": "Add", "parameters": ["a", "b"]})
        evidence = normalize(first)
        self.assertEqual(evidence.evidence_hash, normalize(second).evidence_hash)
        self.assertEqual(evidence.source_mode, "structured_fallback")
        self.assertNotIn("description", evidence.fields)
        self.assertNotIn("instruction", evidence.fields)
        self.assertEqual(evidence.fields["parameters"], ["a", "b"])
        self.assertNotEqual(evidence.evidence_hash, normalize(ToolRecord("x", {**first.documentation, "parameters": ["b", "a"]})).evidence_hash)

    def test_validation_failure_never_marks_pool_ready(self):
        class BadGenerator(MockGenerator):
            def generate(self, evidence, count):
                return GenerationResult([{"query": "same", "scenario_type": "fixture", "generation_notes": "duplicate"}] * count, {})
        with self.assertRaises(PreparationError) as raised:
            prepare_manuals(self.pool, BadGenerator(), ManualCache(self.root), count=2, attempts=2)
        self.assertEqual(raised.exception.report["generation_calls"], 6)
        self.assertEqual(raised.exception.report["valid"], 0)
        self.assertFalse(list(self.root.rglob("*.json")))

    def test_partial_preparation_and_changed_count_require_full_coverage(self):
        generator, cache = CountingGenerator(), ManualCache(self.root)
        with self.assertRaises(PreparationError) as raised:
            prepare_manuals(self.pool, generator, cache, count=2, max_new=1)
        self.assertEqual(raised.exception.report["valid"], 1)
        prepare_manuals(self.pool, generator, cache, count=2)
        self.assertEqual(generator.calls, 3)
        with self.assertRaises(PreparationError):
            prepare_manuals(self.pool, generator, cache, count=3, cache_only=True)
        with self.assertRaises(ValueError):
            build_documents(self.pool, "manual", {})

    def test_bm25_formula_ties_all_and_no_hit(self):
        index = BM25Index({"a": "apple apple", "b": "banana banana", "c": "apple banana"})
        result = index.search("apple", 3)
        self.assertEqual([hit.tool_id for hit in result], ["a", "c"])
        expected = math.log1p((3 - 2 + 0.5) / (2 + 0.5)) * 2 * 2.5 / 3.5
        self.assertAlmostEqual(result[0].score, expected)
        self.assertEqual(index.search("unknown", 2), [])
        self.assertEqual([hit.tool_id for hit in index.search("unknown", None)], ["a", "b", "c"])
        tied = BM25Index({"z": "weather", "a": "weather"})
        self.assertEqual([hit.tool_id for hit in tied.search("weather")], ["a", "z"])
        self.assertIn("weather", tokenize("WeatherForecast"))
        self.assertIn("\u5929\u6c14", tokenize("\u5317\u4eac\u5929\u6c14"))

    def test_manual_changes_ranking_but_not_original_specs(self):
        original = copy.deepcopy(self.pool.tools["calculator"].documentation)
        prepared = prepare_manuals(self.pool, MockGenerator(), ManualCache(self.root), count=2)
        prepared.records["calculator"]["manual"]["proxy_queries"] = ["meteorological forecast", "weather forecast"]
        index = BM25Index(build_documents(self.pool, "manual", prepared.records))
        search = ToolSearch(self.pool, index)
        result = search.select("meteorological", k=1)
        self.assertEqual(result[0].tool_id, "calculator")
        self.assertEqual(search.original_tools(result)[0].documentation, original)
        self.assertEqual(len(search.select("meteorological", budget="all")), 3)

    def test_metrics_multilabel_and_empty_results(self):
        result = evaluate({"q": [RankedTool("wrong", 2), RankedTool("a", 1)]}, {"q": {"a": 1, "b": 1}}, k=2)
        metrics = result["macro_average"]
        self.assertEqual(metrics["recall@2"], 0.5)
        self.assertEqual(metrics["mrr@2"], 0.5)
        self.assertAlmostEqual(metrics["ndcg@2"], (1 / math.log2(3)) / (1 + 1 / math.log2(3)))
        self.assertEqual(evaluate({"q": []}, {"q": {"a": 1}}, k=2)["macro_average"]["recall@2"], 0)

    def write_dataset(self):
        for filename, rows in {
            "tools.jsonl": [{"id": tool.tool_id, "documentation": json.dumps(tool.documentation)} for tool in self.pool.tools.values()],
            "queries.jsonl": [{"id": "q1", "query": "weather forecast", "instruction": "LEAK", "labels": [{"doc": "LEAK"}], "source_dataset": "fixture"}, {"id": "empty", "query": "", "source_dataset": "fixture"}],
            "qrels.jsonl": [{"query_id": "q1", "tool_id": "weather", "relevance": 1}, {"query_id": "empty", "tool_id": "calendar", "relevance": 1}],
        }.items():
            (self.root / filename).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_cli_prepare_evaluate_and_search(self):
        self.write_dataset()
        common = ["--dataset", str(self.root), "--cache", str(self.root / "cache"), "--proxy-count", "2"]
        output = self.root / "result.json"
        self.assertEqual(main(["prepare", *common, "--output", str(output)]), 0)
        self.assertEqual(main(["evaluate", *common, "--with-manuals", "--all-rankings", "--k", "2", "--output", str(output)]), 0)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(report["mock_manuals"])
        self.assertEqual(set(report["representations"]), {"spec", "manual", "spec_manual"})
        self.assertEqual(report["queries"]["empty_query_ids"], ["empty"])
        self.assertIsNone(report["full_library_exposure"]["ranking_metrics"])
        self.assertEqual(report["representations"]["spec"]["top_k"]["per_query"]["empty"]["recall@2"], 0)
        self.assertEqual(main(["search", *common, "--representation", "manual", "--budget", "all", "--query", "weather", "--output", str(output)]), 0)
        self.assertEqual(len(json.loads(output.read_text(encoding="utf-8"))["selected"]), 3)
        self.assertNotIn("LEAK", json.dumps([json.loads(path.read_text()) for path in (self.root / "cache").rglob("*.json")]))

    def test_command_provider_protocol(self):
        script = self.root / "bridge.py"
        script.write_text('import json,sys\nr=json.load(sys.stdin)\nassert "query" not in r and "labels" not in r\nassert r["protocol_version"] == 2\nassert "{N}" not in r["system_prompt"]\nassert "Return exactly 2 objects per skill." in r["system_prompt"]\nname=r["capabilities"][0]["name"]\nprint(json.dumps({"manuals":{name:[{"query":"Intent " + str(i),"scenario_type":"fixture","generation_notes":"audit only"} for i in range(r["proxy_query_count"])]},"usage":{"input_tokens":10}}))\n', encoding="utf-8")
        generator = CommandGenerator({"command": [sys.executable, str(script)], "model": "fixture-model"})
        result = generator.generate(normalize(self.pool.tools["weather"]), 2)
        self.assertEqual(result.proxy_queries, ["Intent 0", "Intent 1"])
        self.assertEqual(result.usage["input_tokens"], 10)
        unnamed = generator.generate(normalize(self.pool.tools["calendar"]), 2)
        self.assertEqual(unnamed.proxy_queries, result.proxy_queries)

    def test_paper_prompt_audit_fields_survive_cache_and_do_not_enter_retrieval(self):
        self.assertEqual(PROMPT_VERSION, "toollery_appendix_a1_v1")
        self.assertIn("Each object must contain query, scenario_type, generation_notes.", SYSTEM_PROMPT)
        class AuditedGenerator(MockGenerator):
            def generate(self, evidence, count):
                return GenerationResult([{"query": "Intent " + str(i), "scenario_type": "AUDIT_ONLY_MARKER", "generation_notes": "AUDIT_ONLY_MARKER"} for i in range(count)], {})
        cache = ManualCache(self.root)
        first = prepare_manuals(self.pool, AuditedGenerator(), cache, count=2)
        second = prepare_manuals(self.pool, AuditedGenerator(), cache, count=2, cache_only=True)
        self.assertEqual(first.records, second.records)
        self.assertEqual(second.report["cache_hits"], 3)
        self.assertEqual(second.records["weather"]["manual"]["entries"][0]["generation_notes"], "AUDIT_ONLY_MARKER")
        for representation in ("manual", "spec_manual"):
            self.assertNotIn("AUDIT_ONLY_MARKER", "\n".join(build_documents(self.pool, representation, second.records).values()))
        record = first.records["weather"]
        record["manual"]["entries"][0]["query"] = "inconsistent cached query"
        record["manual_hash"] = digest(record["manual"])
        cache.path(record["manual_key"]).write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(PreparationError):
            prepare_manuals(self.pool, AuditedGenerator(), cache, count=2, cache_only=True)


if __name__ == "__main__":
    unittest.main()
