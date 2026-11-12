"""Require partial deletion and re-evaluation of changed reader registrations."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from restart_reclamation import RECEIPT, checked, summarize


class RestartReclamationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt = json.loads(RECEIPT.read_bytes())

    def test_controls_and_interrupted_cases_converge(self):
        summary = checked(self.receipt)
        self.assertEqual((summary["trials"], summary["interruptions"]), (20, 16))
        self.assertEqual(summary["snapshot_reads"], 100)

    def test_oracle_rejects_lost_progress_and_stale_eligibility(self):
        runs = deepcopy(self.receipt["runs"])
        partial = next(row for row in runs if row["cut"] == "partial-unsynced")
        partial["after_interruption"] = partial["initial"]
        with self.assertRaisesRegex(ValueError, "inventory oracle"):
            summarize(runs)
        runs = deepcopy(self.receipt["runs"])
        released = next(row for row in runs if row["order"] == "release-oldest"
                        and row["cut"] == "after-sync")
        released["retry"]["removed"] = []
        with self.assertRaisesRegex(ValueError, "inventory oracle"):
            summarize(runs)

    def test_post_drain_repetition_must_be_a_no_op(self):
        runs = deepcopy(self.receipt["runs"])
        runs[0]["no_op"]["removed"] = ["shared.bin"]
        with self.assertRaisesRegex(ValueError, "inventory oracle"):
            summarize(runs)
