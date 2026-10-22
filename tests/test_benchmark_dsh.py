import json
from pathlib import Path
import tempfile
import unittest

from toollery.adapters.dsh import DSHBridge
from toollery.benchmark_dsh import (presentation_schemas, MappedIndex, parse_selection,
    selection_score, stratified_sample, retrieval_rows)
from toollery.data import Dataset, Query, ToolPool, ToolRecord
from toollery.retrieval import BM25Index, RankedTool, ToolSearch


class BenchmarkTests(unittest.TestCase):
    def test_duplicate_names_and_heterogeneous_docs_keep_raw_evidence(self):
        pool = ToolPool([ToolRecord('z', {'name': 'same', 'parameters': ['bad-format']}),
                         ToolRecord('a', {'name': 'same', 'description': 'alpha'}),
                         ToolRecord('b', 'plain text beta')])
        schemas, mapping = presentation_schemas(pool)
        self.assertEqual(list(mapping), ['a', 'b', 'z'])
        self.assertEqual(len(set(mapping.values())), 3)
        for tool, key in zip(schemas, pool.tool_ids):
            self.assertEqual(tool['description'], pool.tools[key].spec_text)
            self.assertEqual(tool['parameters']['properties'], {})
        index = BM25Index({'z': 'match', 'a': 'match', 'b': 'match'})
        mapped = MappedIndex(index, mapping)
        self.assertEqual([hit.tool_id for hit in mapped.search('match')], sorted(mapping.values()))

    def test_sampler_covers_each_stratum_without_changing_query_text(self):
        queries = [Query(str(i), 'original query', 'A' if i < 7 else 'B') for i in range(10)]
        qrels = {q.query_id: {'x': 1, **({'y': 1} if int(q.query_id) % 2 else {})} for q in queries}
        dataset = Dataset(ToolPool([ToolRecord('x', 'x'), ToolRecord('y', 'y')]), queries, qrels, {})
        meta = {q.query_id: {'category': 'code', 'source_dataset': q.source_dataset} for q in queries}
        selected, strata = stratified_sample(dataset, meta, count=6)
        self.assertEqual(len(set(selected)), 6)
        self.assertTrue(all(row['sample'] > 0 for row in strata))
        self.assertEqual(stratified_sample(dataset, meta, count=6)[0], selected)
        self.assertTrue(all(q.text == 'original query' for q in queries))

    def test_selection_parser_rejects_hallucinated_duplicates_and_malformed_ids(self):
        for text in ('{"tool_ids":["outside"]}', '{"tool_ids":["a","a"]}',
                     '{"tool_ids":"a"}', 'use a', '{"tool_ids":[3]}'):
            self.assertIsNotNone(parse_selection(text, ['a'])[1])
        self.assertEqual(parse_selection('```json\n{"tool_ids":["a"]}\n```', ['a']), (['a'], None))
        self.assertEqual(selection_score(['a', 'wrong'], {'a': 1, 'b': 1})['f1'], .5)

    def test_complete_coverage_distinguishes_any_hit_and_all_positives(self):
        dataset = Dataset(ToolPool([ToolRecord('a', 'a'), ToolRecord('b', 'b')]),
                          [Query('q', 'text', 'source')], {'q': {'a': 1, 'b': 1}}, {})
        row = retrieval_rows({'q': [RankedTool('a', 1)]}, dataset)['q']
        self.assertEqual(row['success@3'], 1)
        self.assertEqual(row['recall@3'], .5)
        self.assertEqual(row['complete@3'], 0)

    def test_compact_audit_keeps_incremental_jsonl_without_repeating_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = DSHBridge(Path(directory), representation='spec', compact_audit=True)
            schemas = [{'name': 'a', 'description': 'alpha', 'parameters': {'type': 'object'}}]
            snapshot = bridge.capture('sid', schemas)
            state = bridge.sessions['sid']
            state.ready = True
            state.searches['spec'] = ToolSearch(state.pool, BM25Index({'a': 'alpha'}))
            payload = {'session_id': 'sid', 'snapshot_hash': snapshot['snapshot_hash'], 'prompt': 'alpha'}
            for _ in range(3):
                result = bridge.select(payload)
                self.assertNotIn('tool_details', result)
                bridge.observe({'selection_id': result['selection_id'], 'model_tool_names': ['a']})
            self.assertFalse((Path(directory) / 'selections.json').exists())
            lines = (Path(directory) / 'selections.jsonl').read_text('utf-8').splitlines()
            self.assertEqual(len(lines), 3)
            self.assertTrue(all(json.loads(line)['selected_names'] == ['a'] for line in lines))


if __name__ == '__main__':
    unittest.main()
