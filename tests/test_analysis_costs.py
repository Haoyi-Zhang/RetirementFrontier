"""Statistical checks for retained, paired analysis-cost observations."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('analysis_costs', Path(__file__).resolve().parents[1] / 'scripts/analysis_costs.py')
costs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(costs)


class AnalysisCostTests(unittest.TestCase):
    def test_case_medians_use_all_repetitions(self):
        row = {'samples_cpu_ns': {name: [dict(cpu_ns=n, calls=2) for n in range(1, 9)] for name in costs.METHODS}}
        row['samples_cpu_ns']['pair'] = [dict(cpu_ns=2*n, calls=2) for n in range(1, 9)]
        result = costs.case_statistics(row)
        self.assertEqual(result['frontier'], 2.25)
        self.assertEqual(result['pair_over_frontier'], 2.0)

    def test_incomplete_or_nonpositive_samples_rejected(self):
        for values in ([1] * 7, [1] * 7 + [0], [1] * 7 + [True]):
            row = {'samples_cpu_ns': {name: [dict(cpu_ns=v, calls=1) for v in values] for name in costs.METHODS}}
            with self.assertRaises(ValueError):
                costs.case_statistics(row)


if __name__ == '__main__':
    unittest.main()
