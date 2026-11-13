from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from spark_checkpoint_validation import RECEIPT, load, summarize


class SparkCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.data = load()
        self.receipt = json.loads(RECEIPT.read_bytes())

    def test_retains_unchanged_partitions_and_both_directions(self):
        rows = summarize(self.receipt, self.data)
        self.assertEqual(rows["agg"], dict(partitions=5, changed=3, trials_per_policy=10,
                                          early=7, late=10, union=10))
        self.assertEqual(rows["dedup"], dict(partitions=5, changed=2, trials_per_policy=10,
                                            early=8, late=10, union=10))

    def test_cannot_omit_a_partition(self):
        receipt = deepcopy(self.receipt)
        receipt["cases"].pop()
        with self.assertRaises(ValueError):
            summarize(receipt, self.data)

    def test_cannot_change_evidence_or_reclassify_failure(self):
        damaged = dict(self.data)
        damaged["agg/commits/3"] += b"\n"
        with self.assertRaisesRegex(ValueError, "unbound"):
            summarize(self.receipt, damaged)
        receipt = deepcopy(self.receipt)
        failing = next(r for c in receipt["cases"] for r in c["comparisons"] if r["missing"])
        failing["status"] = "ok"
        with self.assertRaisesRegex(ValueError, "coverage"):
            summarize(receipt, self.data)
