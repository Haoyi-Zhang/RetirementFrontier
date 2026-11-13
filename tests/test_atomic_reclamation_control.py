"""Validate the evidence for the transaction-owned retirement control."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from atomic_reclamation_control import RECEIPT, checked


class AtomicReclamationControlTests(unittest.TestCase):
    def setUp(self):
        self.receipt = json.loads(RECEIPT.read_bytes())

    def test_native_control_distinguishes_commit_scope(self):
        summary = checked(self.receipt)
        self.assertEqual(summary["trials"], 16)
        self.assertEqual(summary["intact_catalogs"], 16)
        exact = {key: [record[stage]["exact"] for stage in ("before-commit", "after-commit")]
                 for key, record in summary["domains"].items()}
        self.assertEqual(exact, {"sqlite-rows": [4, 4], "external-files": [0, 4]})

    def test_false_recovery_and_missing_cell_are_rejected(self):
        bad = deepcopy(self.receipt)
        row = next(row for row in bad["observations"] if row["observed"]["missing"])
        row["observed"]["missing"] = []
        with self.assertRaisesRegex(ValueError, "scope/cut oracle"):
            checked(bad)
        bad = deepcopy(self.receipt)
        bad["observations"].pop()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            checked(bad)
