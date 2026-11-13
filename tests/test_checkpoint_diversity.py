"""Guard the paired-workload evidence against outcome or accounting drift."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from checkpoint_diversity import RECEIPT, summarize, validate


class CheckpointDiversityEvidence(unittest.TestCase):
    def setUp(self):
        self.receipt = json.loads(RECEIPT.read_bytes())

    def test_two_workloads_keep_distinct_units_and_results(self):
        cases = validate(self.receipt)["cases"]
        self.assertEqual([(c["keys"], c["summary"]["union_early_bytes"]) for c in cases],
                         [(144, 4196), (40, 3086)])
        self.assertTrue(all(c["summary"]["exact_restores"] == 6 for c in cases))

    def test_false_success_is_rejected(self):
        case = self.receipt["cases"][0]
        next(r for r in case["restores"] if r["missing"])["status"] = "exact"
        with self.assertRaisesRegex(ValueError, "disagrees with inventory"):
            summarize(case)

    def test_missing_pair_or_incorrect_reclaimed_bytes_is_rejected(self):
        case = self.receipt["cases"][0]
        case["restores"].pop()
        with self.assertRaisesRegex(ValueError, "incomplete paired retention"):
            summarize(case)
        case = self.receipt["cases"][1]
        next(r for r in case["restores"] if r["policy"] == "union")["early_bytes"] += 1
        with self.assertRaisesRegex(ValueError, "disagrees with inventory"):
            summarize(case)
