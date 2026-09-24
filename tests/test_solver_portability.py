from __future__ import annotations
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import solver


class SolverPortabilityTests(unittest.TestCase):
    def test_system_library_does_not_require_python_package(self):
        with patch("solver.ctypes.util.find_library", return_value="libz3.so.4"), patch("solver.importlib.util.find_spec", return_value=None):
            self.assertEqual(solver._library_candidates(), ["libz3.so.4"])

    def test_explicit_skip_prevents_library_loading(self):
        with patch.dict(os.environ, {"RF_SKIP_SMT": "1"}), patch("solver.ctypes.CDLL") as loader:
            self.assertFalse(solver.solver_available())
            loader.assert_not_called()

    def test_wheel_bundled_library_is_discovered(self):
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td) / "lib/libz3.so"
            lib.parent.mkdir(); lib.touch()
            spec = types.SimpleNamespace(submodule_search_locations=[td])
            with patch("solver.ctypes.util.find_library", return_value=None), patch("solver.importlib.util.find_spec", return_value=spec):
                self.assertEqual(solver._library_candidates(), [str(lib)])

    def test_unavailable_probe_is_false_not_success(self):
        with patch("solver._library_candidates", return_value=[]):
            self.assertFalse(solver.solver_available())
