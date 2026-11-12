"""Offline checks for the paired native checkpoint receipt and cleanup controls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_native_checkpoints import RECEIPT, checked, evaluate


class NativeCheckpointsTest(unittest.TestCase):
    def setUp(self):
        self.receipt = json.loads(RECEIPT.read_bytes())

    def test_candidate_guard_is_exact_and_less_conservative(self):
        summary = checked(self.receipt)
        self.assertEqual(summary["subsets"], 2 ** summary["catalog_files"])
        scores = summary["policies"]
        exact = scores["hold-candidate-union"]
        self.assertEqual((exact["unsafe_allowed"], exact["safe_blocked"]), (0, 0))
        self.assertEqual(exact["allowed"], 2 ** len(summary["archived_only"]))
        self.assertEqual(scores["hold-all"]["safe_blocked"], exact["allowed"] - 1)
        self.assertGreater(scores["assume-before"]["unsafe_allowed"], 0)
        self.assertGreater(scores["assume-after"]["unsafe_allowed"], 0)

    def test_missing_shared_table_and_unrecovered_view_fail(self):
        shared, = checked(self.receipt)["shared_files"]
        native = deepcopy(self.receipt["native"])
        native["before"].pop(shared)
        with self.assertRaisesRegex(ValueError, "shape"):
            evaluate(native)
        native = deepcopy(self.receipt["native"])
        native["after"][shared] += 1
        with self.assertRaisesRegex(ValueError, "shared file changed size"):
            evaluate(native)
        native = deepcopy(self.receipt["native"])
        native["post_disposal_reopens"] = 1
        with self.assertRaisesRegex(ValueError, "client observations"):
            evaluate(native)

    def test_rejects_tampered_archive_evidence(self):
        native = deepcopy(self.receipt["native"])
        native["archived_unlinked"] = 0
        with self.assertRaisesRegex(ValueError, "native archive disposal differs"):
            evaluate(native)
        self.receipt["summary"]["retired_sst_bytes"] += 1
        with self.assertRaisesRegex(ValueError, "stale"):
            checked(self.receipt)
