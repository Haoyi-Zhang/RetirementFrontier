"""Check the offline comparison without requiring RocksDB to be installed."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rocksdb_lifecycle import RECEIPT, checked_case, checked_receipt


class RocksDBLifecycleTest(unittest.TestCase):
    def test_retained_observations_and_file_witnesses(self):
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        for case in checked_receipt(receipt):
            before, after = (set(case["observations"][index]["live"]) for index in (1, 4))
            outcome = case["two_view_projection"]
            self.assertEqual(set(outcome["required"]), before | after)
            self.assertEqual(set(outcome["missing_if_keep_before_only"]), after - before)
            self.assertEqual(set(outcome["missing_if_keep_after_only"]), before - after)
            for omitted in before | after:
                self.assertTrue(before - ((before | after) - {omitted})
                                or after - ((before | after) - {omitted}))

    def test_reopen_change_or_missing_file_fails_admission(self):
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        case = receipt["cases"][0]
        rows = deepcopy(case["observations"])
        rows[4]["sequence"] += 1
        with self.assertRaisesRegex(ValueError, "logical workload"):
            checked_case(case["batch_size"], rows)
        rows = deepcopy(case["observations"])
        rows[4]["disk"].pop(next(iter(rows[4]["live"])))
        with self.assertRaisesRegex(ValueError, "unavailable table"):
            checked_case(case["batch_size"], rows)

    def test_mismatched_source_or_projection_fails_verification(self):
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        altered = deepcopy(receipt)
        altered["sources"] = {}
        with self.assertRaisesRegex(ValueError, "source"):
            checked_receipt(altered)
        altered = deepcopy(receipt)
        altered["cases"][0]["two_view_projection"]["required"] = []
        with self.assertRaisesRegex(ValueError, "differs"):
            checked_receipt(altered)
