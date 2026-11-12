"""Regression checks for composed publication and reader protection."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from combined_reclamation import RECEIPT, checked, fixture, protected_plan, summarize


class CombinedReclamationTest(unittest.TestCase):
    def test_pending_and_older_pins_cover_different_files(self):
        views, data, _ = fixture()
        files = {name: len(value) for name, value in data.items()}
        ticket = {"state": "pending", "candidates": [1, 2], "layouts": views}
        self.assertEqual(protected_plan(ticket, [0], files), ["archive.bin"])
        self.assertEqual(protected_plan(ticket, [], files),
                         ["archive.bin", "old-left.bin", "old-right.bin"])
        with self.assertRaisesRegex(ValueError, "invalid pin epoch"):
            protected_plan(ticket, [False], files)
        del files["latest-left.bin"]
        with self.assertRaisesRegex(ValueError, "protected segment missing"):
            protected_plan(ticket, [0], files)
        ticket["candidates"] = [1]
        with self.assertRaisesRegex(ValueError, "incomplete or invalid"):
            protected_plan(ticket, [0], files)

    def test_receipt_requires_real_contention_and_expected_retirement(self):
        value = json.loads(RECEIPT.read_bytes())
        self.assertEqual(checked(value)["contended_registrations"], 4)
        altered = deepcopy(value["runs"])
        altered[0]["admission"] = "available"
        with self.assertRaisesRegex(ValueError, "inconsistent process"):
            summarize(altered)
        altered = deepcopy(value["runs"])
        altered[1]["phases"][0]["removed"].append("old-left.bin")
        with self.assertRaisesRegex(ValueError, "independent combined live set"):
            summarize(altered)
