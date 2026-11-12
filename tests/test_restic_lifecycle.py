"""Check the retained native observation and reject inconsistent evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from restic_lifecycle import RECEIPT, check


class ResticLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))

    def test_summary_is_supported_by_retained_native_stages(self):
        result = check(self.receipt)
        self.assertEqual(result["snapshots_restored"], 4)
        self.assertGreater(result["surviving_data_blobs_relocated"], 0)

    def test_rejects_missing_restore_and_missing_pack(self):
        original = deepcopy(self.receipt)
        self.receipt["observations"][0]["restores"].pop("first")
        with self.assertRaisesRegex(ValueError, "not all retained"):
            check(self.receipt)
        after = original["observations"][-1]
        after["snapshots"]["second"][next(iter(after["snapshots"]["second"]))] = ["absent"]
        with self.assertRaisesRegex(ValueError, "missing pack"):
            check(original)
