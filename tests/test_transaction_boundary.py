"""Keep the catalog guarantee distinct from external object reconstruction."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from transaction_boundary import RECEIPT, checked


class TransactionBoundaryEvidence(unittest.TestCase):
    def test_intact_catalogs_do_not_imply_intact_objects(self):
        receipt = json.loads(RECEIPT.read_bytes())
        totals = checked(receipt)
        self.assertEqual(totals["intact_catalogs"], 24)
        self.assertEqual(totals["policies"], {
            "keep-before": {"trials": 8, "object_failures": 4},
            "keep-after": {"trials": 8, "object_failures": 4},
            "keep-both": {"trials": 8, "object_failures": 0},
        })

    def test_removing_an_observed_object_failure_is_rejected(self):
        receipt = json.loads(RECEIPT.read_bytes())
        row = next(x for x in receipt["observations"] if x["missing_segments"])
        row["missing_segments"] = []
        with self.assertRaisesRegex(ValueError, "actual file probes disagree"):
            checked(receipt)
