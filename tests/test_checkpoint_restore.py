"""Distinguish native reopen failures from successful byte-exact restores."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from checkpoint_restore import RECEIPT, checked


class NativeCheckpointRestoreEvidence(unittest.TestCase):
    def test_one_native_open_failure_for_each_single_view_rule(self):
        receipt = json.loads(RECEIPT.read_bytes())
        summary = checked(receipt)
        self.assertEqual(summary["failures"], {"keep-before": 1, "keep-after": 1, "keep-both": 0})
        self.assertEqual(summary["exact_reopens"], 4)
        self.assertEqual([row["native_status"] for row in receipt["rows"] if row["missing"]],
                         ["open-failed", "open-failed"])

    def test_missing_table_cannot_be_counted_as_a_successful_restore(self):
        receipt = json.loads(RECEIPT.read_bytes())
        next(row for row in receipt["rows"] if row["missing"])["native_status"] = "ok:64"
        with self.assertRaisesRegex(ValueError, "native restore did not agree"):
            checked(receipt)
