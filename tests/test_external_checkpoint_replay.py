import copy
import gzip
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from external_checkpoint_replay import INPUT, RECEIPT, operations, summarize, validate


class ExternalCheckpointReplayTests(unittest.TestCase):
    def setUp(self):
        self.traces = json.loads(gzip.decompress(INPUT.read_bytes()))
        self.receipt = json.loads(RECEIPT.read_bytes())

    def test_bound_receipt_includes_unchanged_and_rewritten_tables(self):
        cases = validate(self.receipt, self.traces)["cases"]
        self.assertEqual([c["summary"]["single_exact"] for c in cases.values()], [2, 2, 4, 2])
        for case in cases.values():
            self.assertEqual(case["summary"]["union_exact"], 2)
            self.assertEqual(case["summary"]["hold_exact"], 2)

    def test_an_omitted_table_cannot_be_reported_as_exact(self):
        case = copy.deepcopy(self.receipt["cases"]["012"])
        failed = next(row for row in case["restores"] if row["missing"])
        failed["status"] = "exact"
        with self.assertRaisesRegex(ValueError, "file inventory"):
            summarize(case)

    def test_reordered_or_dropped_write_is_rejected(self):
        case = copy.deepcopy(self.traces["015"])
        case["requests"][0], case["requests"][1] = case["requests"][1], case["requests"][0]
        with self.assertRaisesRegex(ValueError, "projected request|first-occurrence"):
            operations(case)
        case = copy.deepcopy(self.traces["015"])
        case["requests"].pop()
        with self.assertRaisesRegex(ValueError, "census"):
            operations(case)
