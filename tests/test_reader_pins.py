"""Check fail-closed pin accounting and bind the matched native trials."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from reader_pins import RECEIPT, checked, fixture, plan, summarize


class ReaderPinsTest(unittest.TestCase):
    def test_unpinning_one_epoch_does_not_release_the_other(self):
        views, data, _ = fixture()
        files = {name: len(content) for name, content in data.items()}
        self.assertEqual(plan(views[2], views[:2], files), ["archive.bin"])
        self.assertEqual(plan(views[2], [views[0]], files), ["archive.bin", "middle.bin"])
        self.assertEqual(plan(views[2], [views[1]], files),
                         ["archive.bin", "old-left.bin", "old-right.bin"])
        del files["old-left.bin"]
        with self.assertRaisesRegex(ValueError, "protected segment missing"):
            plan(views[2], [views[0]], files)

    def test_retained_data_detects_early_removal_or_missing_reads(self):
        value = json.loads(RECEIPT.read_bytes())
        self.assertEqual(checked(value)["snapshot_reads"], 12)
        altered = deepcopy(value["trials"])
        altered[1]["phases"][0]["removed"].append("middle.bin")
        with self.assertRaisesRegex(ValueError, "pin phase"):
            summarize(altered, value["stale"])
        altered = deepcopy(value["trials"])
        altered[0]["middle_snapshot_reads"] = 0
        with self.assertRaisesRegex(ValueError, "missing native"):
            summarize(altered, value["stale"])

    def test_process_exit_does_not_count_as_a_release(self):
        value = json.loads(RECEIPT.read_bytes())
        self.assertEqual(checked(value)["stale_protected_files"], 2)
        value["stale"]["pin_epochs"].clear()
        with self.assertRaisesRegex(ValueError, "stale pin"):
            summarize(value["trials"], value["stale"])
