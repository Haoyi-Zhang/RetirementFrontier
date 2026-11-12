"""Recheck the paper's worked examples entirely within the finite model."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from witness_examples import native_comparison, shared_comparison


class WitnessExamples(unittest.TestCase):
    def test_frontier_root_is_not_a_cardinality_objective(self):
        row = shared_comparison()
        self.assertEqual(row["frontier_cut"], list(range(6)))
        self.assertEqual(row["minimum_cut"], [0, 1, 2, 5])
        self.assertEqual(row["selected_roots"], [4, 2])
        self.assertEqual(row["forced_user_cut"], row["frontier_cut"])
        self.assertEqual((row["repaired_free"], row["retiring_guard"]), (7, 6))

    def test_native_projection_distinguishes_both_missing_edges(self):
        self.assertEqual(native_comparison(), {
            "unguarded-retirement": dict(view="epoch:before", missing=["000009.sst"], durable_events=7),
            "unguarded-publication": dict(view="epoch:after", missing=["000022.sst"], durable_events=8),
        })
