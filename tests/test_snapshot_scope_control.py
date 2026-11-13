"""Check evidence and fail closed on an altered WAL snapshot outcome."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from snapshot_scope_control import RECEIPT, checked


class SnapshotScopeTests(unittest.TestCase):
    def test_only_current_view_cleanup_breaks_the_live_reader(self):
        data = json.loads(RECEIPT.read_bytes())
        observed = checked(data)
        self.assertEqual({name: r["old_exact"] for name, r in observed.items()},
                         {"sqlite-rows": 2, "current-only": 0, "retain-reader": 2})
        self.assertTrue(all(r["new_exact"] == r["release_exact"] == r["trials"] == 2
                            for r in observed.values()))
        trial = next(r for r in data["trials"] if r["policy"] == "current-only")
        trial["old"]["missing"] = []
        with self.assertRaisesRegex(ValueError, "lifetime oracle"):
            checked(data)
