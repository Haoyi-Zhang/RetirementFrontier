"""Check the paired diagnostic baseline and its manuscript receipt."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from diagnostic_census import RECEIPT, checked, compare
from engine import census_template
from model import admit_trace
from witness_examples import shared_chunk


class DiagnosticCensus(unittest.TestCase):
    def test_latest_user_is_two_events_above_the_exact_minimum(self):
        self.assertEqual(compare(admit_trace(shared_chunk("no-guard"))), (2, True))

    def test_fully_ordered_control_is_safe(self):
        self.assertEqual(compare(admit_trace(census_template("retired-free", (1 << 15) - 1))),
                         (None, False))

    def test_receipt_detects_inconsistent_histogram(self):
        receipt = json.loads(RECEIPT.read_bytes())
        checked(receipt)
        receipt["families"]["shared"]["excess_histogram"]["0"] += 1
        with self.assertRaisesRegex(ValueError, "inconsistent diagnostic counts"):
            checked(receipt)
