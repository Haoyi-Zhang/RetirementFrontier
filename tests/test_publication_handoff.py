"""Check that the publication cleaner fails closed and retained outcomes bind."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publication_handoff import RECEIPT, certify_pending, checked, fixture, summarize


class PublicationHandoffTest(unittest.TestCase):
    def test_pending_claim_requires_both_views_and_available_files(self):
        ticket, data, _ = fixture("split")
        inventory = {key: len(value) for key, value in data.items()}
        self.assertEqual(certify_pending(ticket, inventory), ["archive-extra.bin", "archive.bin"])
        invalid = deepcopy(ticket)
        del invalid["views"]["1"]
        with self.assertRaisesRegex(ValueError, "incomplete candidate"):
            certify_pending(invalid, inventory)
        invalid = deepcopy(ticket)
        invalid["views"]["0"]["alpha"] = ["../escape.bin"]
        with self.assertRaisesRegex(ValueError, "invalid segment"):
            certify_pending(invalid, inventory)
        del inventory["whole.bin"]
        with self.assertRaisesRegex(ValueError, "unavailable segment"):
            certify_pending(ticket, inventory)

    def test_matched_histories_do_not_leak_commit_outcome(self):
        value = json.loads(RECEIPT.read_bytes())
        self.assertEqual(checked(value)["native_reopens"], 32)
        value["runs"][0]["visible"]["reply_received"] = True
        with self.assertRaisesRegex(ValueError, "exposed"):
            summarize(value["runs"])

    def test_rejects_incorrect_epoch_or_premature_reclamation(self):
        value = json.loads(RECEIPT.read_bytes())
        altered = deepcopy(value["runs"])
        altered[0]["selected_epoch"] = 1
        with self.assertRaisesRegex(ValueError, "inconsistent native"):
            summarize(altered)
        value["runs"][1]["before_resolution"].append("whole.bin")
        with self.assertRaisesRegex(ValueError, "cleanup order"):
            summarize(value["runs"])
