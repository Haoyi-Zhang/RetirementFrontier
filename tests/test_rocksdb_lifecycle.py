"""Check the offline comparison without requiring RocksDB to be installed."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rocksdb_lifecycle import RECEIPT, checked_case, checked_receipt
from check_native_epochs import RESULT, check_uncertainty, checked, required_while_unresolved


class RocksDBLifecycleTest(unittest.TestCase):
    def test_retained_observations_and_file_witnesses(self):
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        for case in checked_receipt(receipt):
            before, after = (set(case["observations"][index]["live"]) for index in (1, 4))
            outcome = case["two_view_projection"]
            self.assertEqual(set(outcome["required"]), before | after)
            self.assertEqual(set(outcome["shared"]), before & after)
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
            checked_case(case["scope"], case["batch_size"], rows)
        rows = deepcopy(case["observations"])
        rows[4]["disk"].pop(next(iter(rows[4]["live"])))
        with self.assertRaisesRegex(ValueError, "unavailable table"):
            checked_case(case["scope"], case["batch_size"], rows)

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

    def test_unresolved_requires_both_views_and_protects_shared_files(self):
        before, after = {"old-a", "old-b", "shared"}, {"new", "shared"}
        self.assertEqual(required_while_unresolved((before, after)), before | after)
        with self.assertRaisesRegex(ValueError, "at least one"):
            required_while_unresolved(())
        self.assertEqual(check_uncertainty(before, after), {
            "subsets_per_state": 16,
            "safe_subsets": {"before-confirmed": 2, "unresolved": 1, "after-confirmed": 4},
            "unsafe_subsets_accepted_while_unresolved": {
                "candidate-union": 0, "assume-before": 1, "assume-after": 3,
            },
        })

    def test_range_receipt_records_shared_table_and_detects_corrupted_guard_count(self):
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        epochs = json.loads(RESULT.read_text(encoding="utf-8"))
        self.assertGreater(checked(epochs, receipt)["range_ordered_cuts"], 0)
        ranged, = [case for case in epochs["cases"] if case["scope"] == "range"]
        shared = ranged["before"]["files"].keys() & ranged["after"]["files"].keys()
        self.assertEqual(len(shared), 1)
        self.assertTrue(all(f"retire:{file}" != removal
                            for file in shared
                            for removal, _ in ranged["outcomes"]["ordered"]["certificate_guards"]))
        ranged["uncertainty"]["unsafe_subsets_accepted_while_unresolved"]["assume-before"] = 0
        with self.assertRaisesRegex(ValueError, "stale"):
            checked(epochs, receipt)
