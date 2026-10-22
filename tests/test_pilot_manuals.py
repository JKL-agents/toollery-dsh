import unittest
from toollery.data import Dataset, Query, ToolPool, ToolRecord
from scripts.pilot_manuals import eligible_queries, select_tools


class PilotTests(unittest.TestCase):
    def test_selection_deterministic_and_covers_sources_and_evidence_modes(self):
        records = [ToolRecord(f"{s}_tool_{n}", {"name": str(n), **({"description": "capability"} if n % 2 else {"parameters": ["x"]})})
                   for s in ("a", "b", "c") for n in range(8)]
        a = select_tools(ToolPool(records), count=6)
        b = select_tools(ToolPool(list(reversed(records))), count=6)
        self.assertEqual(a, b)
        self.assertEqual(len(set(a)), 6)
        for source in ("a", "b", "c"):
            chosen = [int(t.rsplit("_", 1)[1]) for t in a if t.startswith(source)]
            self.assertEqual(len(chosen), 2)
            self.assertEqual({n % 2 for n in chosen}, {0, 1})

    def test_query_eligibility_keeps_all_original_positives(self):
        pool = ToolPool([ToolRecord("a", "a"), ToolRecord("b", "b")])
        dataset = Dataset(pool, [Query("single", "x", "s"), Query("multi", "y", "s")],
                          {"single": {"a": 1}, "multi": {"a": 1, "b": 1}}, {})
        self.assertEqual([q.query_id for q in eligible_queries(dataset, ["a"])], ["single"])
        self.assertEqual(len(eligible_queries(dataset, ["a", "b"])), 2)
        self.assertEqual(dataset.qrels["multi"], {"a": 1, "b": 1})
