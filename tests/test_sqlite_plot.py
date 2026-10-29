"""Validate the cost figure's statistic without importing plotting packages."""
import copy
import importlib.util
import json
from pathlib import Path
import statistics
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('sqlite_plot', ROOT/'plots/draw_sqlite_costs.py')
PLOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLOT)


class SQLitePlot(unittest.TestCase):
    def setUp(self):
        self.rows = [json.loads(s) for s in (ROOT/'results/sqlite-adapter/costs.jsonl').read_text().splitlines()]

    def test_primary_row_statistic(self):
        values = PLOT.ratios(self.rows)
        for mode in ('DELETE', 'WAL'):
            for family in ('shared', 'unique', 'churn', 'backup'):
                for field in ('slots', 'bytes_after_checkpoint'):
                    def med(policy):
                        return statistics.median(r[field] for r in self.rows
                            if (r['mode'], r['family'], r['policy']) == (mode, family, policy))
                    self.assertEqual(values[mode][family][field], med('frontier-reuse')/med('no-reuse'))
        self.assertEqual(values['DELETE']['shared']['slots'], 5/64)

    def test_duplicate_seed_rejected(self):
        rows = copy.deepcopy(self.rows)
        group = [r for r in rows if (r['mode'], r['family'], r['policy']) == ('DELETE','shared','no-reuse')]
        group[1]['seed'] = group[0]['seed']
        with self.assertRaises(ValueError): PLOT.ratios(rows)

    def test_missing_row_rejected(self):
        with self.assertRaises(ValueError): PLOT.ratios(self.rows[:-1])

    def test_nonpositive_cost_rejected(self):
        rows = copy.deepcopy(self.rows)
        for r in rows: r['slots'] = 0
        with self.assertRaises(ValueError): PLOT.ratios(rows)


if __name__ == '__main__': unittest.main()
