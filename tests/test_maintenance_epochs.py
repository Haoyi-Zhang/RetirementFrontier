"""Same-prefix maintenance: independently check cuts, bytes, and certificates."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from checker import analyze_model, pair_analyze_model
from fallback import fixed_candidate_model, publication_fallback_model
from medium import materialize, recover
from model import AdmissionError, admit_trace
from oracle import exhaustive_model
from targeted_cases import ack, data, ref, root, trace
from verifier import VerificationError, verify_certificate


def maintenance(*, publish=True, retire=True):
    content = b"same bytes"
    older = {"x": [ref(0, content)]}
    newer = {"x": [ref(2, content)]}
    old_m, new_m = (json.dumps(ns, sort_keys=True, separators=(",", ":")).encode()
                    for ns in (older, newer))
    values = {"x": content.hex()}
    return trace(
        [{"op": "put", "name": "x", "chunks_hex": [content.hex()]}],
        [data(0, 0, content), data(1, 1, old_m, "manifest"),
         root(2, 1, older, ref(1, old_m), values, [0, 1]),
         data(3, 2, content), data(4, 3, new_m, "manifest"),
         root(5, 1, newer, ref(3, new_m), values, [2, 3, 4] if publish else [2, 4]),
         {"id": 6, "kind": "free", "slot": 0, "generation": 1,
          "deps": [5] if retire else [2]}],
        [ack(7, 1, [6])],
    )


class MaintenanceEpochTests(unittest.TestCase):
    def test_equal_prefix_is_not_equal_physical_version(self):
        model = admit_trace(maintenance())
        before = recover(materialize(model, [0, 1, 2]))
        after = recover(materialize(model, range(model.n)))
        self.assertTrue(before["ok"] and after["ok"])
        self.assertEqual((before["prefix"], after["prefix"]), (1, 1))
        self.assertEqual(before["objects_hex"], after["objects_hex"])
        report = analyze_model(model, minimum=True)
        self.assertTrue(report["safe"])
        self.assertIn({"writer": 6, "guard_root": 5}, report["certificate"]["retirement"])
        self.assertTrue(verify_certificate(model.trace, report["certificate"])["accepted"])
        certificate = deepcopy(report["certificate"])
        certificate["retirement"][0]["guard_root"] = 2
        with self.assertRaises(VerificationError):
            verify_certificate(model.trace, certificate)

    def test_missing_edges_against_independent_oracle(self):
        for publish in (True, False):
            for retire in (True, False):
                with self.subTest(publish=publish, retire=retire):
                    model = admit_trace(maintenance(publish=publish, retire=retire))
                    front = analyze_model(model, minimum=True)
                    pair = pair_analyze_model(model, minimum=True)
                    oracle = exhaustive_model(model)
                    self.assertEqual(front["safe"], publish and retire)
                    for other in (pair, oracle):
                        self.assertEqual(front["safe"], other["safe"])
                        self.assertEqual(front["minimum_bad_cut"], other["minimum_bad_cut"])
                    if publish:
                        self.assertEqual(publication_fallback_model(model)["safe"], retire)
                        self.assertEqual(fixed_candidate_model(model)["safe"], retire)

    def test_reject_decreasing_prefix_and_changed_same_prefix_content(self):
        decreasing = maintenance()
        decreasing["events"][5]["prefix"] = 0
        with self.assertRaisesRegex(AdmissionError, "prefixes must not decrease"):
            admit_trace(decreasing)
        with self.assertRaisesRegex(VerificationError, "prefixes must not decrease"):
            verify_certificate(decreasing, {})
        wrong = maintenance()
        wrong["events"][5]["objects_hex"]["x"] = b"changed".hex()
        with self.assertRaises(AdmissionError):
            admit_trace(wrong)
