"""Dependency-free checks against the actual payout tax functions."""
import ast
import math
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'app/api/routes/payouts.py'
tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {'money', 'monthly_tds_for', 'professional_tax_for', 'apply_monthly_salary_breakdown', 'apply_payable_adjustment', 'validated_adjustment_amount'}]
class TestHttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
namespace = {'math': math, 'tax_settings': lambda db: db, 'HTTPException': TestHttpError}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), namespace)


class MonthlyTdsTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(tds_threshold=100000, tds_rate=10,
            income_threshold=25000, regular_month_amount=200, march_amount=300)

    def test_threshold_boundaries(self):
        for income, expected in [(0, 0), (99999, 0), (100000, 0), (100000.01, 10000), (120000, 12000)]:
            with self.subTest(income=income):
                self.assertEqual(namespace['monthly_tds_for'](self.settings, income), expected)

    def test_custom_rate_and_threshold(self):
        self.settings.tds_threshold = 50000
        self.settings.tds_rate = 5
        self.assertEqual(namespace['monthly_tds_for'](self.settings, 60000), 3000)

    def test_disabled_rate(self):
        self.settings.tds_rate = 0
        self.assertEqual(namespace['monthly_tds_for'](self.settings, 120000), 0)

    def test_tds_before_professional_tax(self):
        for month, expected_pt in [(2, 200), (3, 300)]:
            pt = namespace['professional_tax_for'](self.settings, 120000, date(2026, month, 1))
            tds = namespace['monthly_tds_for'](self.settings, 120000)
            self.assertEqual(pt, expected_pt)
            self.assertEqual(tds, 12000)
            self.assertEqual(120000 - pt - tds, 108000 - expected_pt)

    def test_addition_changes_only_payable_once(self):
        row = SimpleNamespace(payout_month=date(2026, 11, 1))
        namespace['apply_monthly_salary_breakdown'](self.settings, row, 144.07, 200)
        self.assertEqual(row.actual_salary, 144.07)
        self.assertEqual(row.payable_amount, 344.07)
        namespace['apply_monthly_salary_breakdown'](self.settings, row, 144.07, 200)
        self.assertEqual(row.payable_amount, 344.07)

    def test_adjustment_does_not_change_tax_thresholds(self):
        row = SimpleNamespace(payout_month=date(2026, 11, 1))
        namespace['apply_monthly_salary_breakdown'](self.settings, row, 100000, 200)
        self.assertEqual(row.actual_salary, 100000)
        self.assertEqual(row.tds_amount, 0)
        self.assertEqual(row.professional_tax_amount, 200)
        self.assertEqual(row.payable_amount, 100000)
        namespace['apply_monthly_salary_breakdown'](self.settings, row, 25100, -200)
        self.assertEqual(row.professional_tax_amount, 200)
        self.assertEqual(row.actual_salary, 25100)
        self.assertEqual(row.payable_amount, 24700)

    def test_reject_negative_payable(self):
        row = SimpleNamespace(payout_month=date(2026, 11, 1))
        with self.assertRaises(TestHttpError):
            namespace['apply_monthly_salary_breakdown'](self.settings, row, 100, -101)

    def test_repeated_adjustments_preserve_saved_salary_and_tax(self):
        row = SimpleNamespace(payout_month=date(2026, 11, 1))
        namespace['apply_monthly_salary_breakdown'](self.settings, row, 120000, 0)
        namespace['apply_payable_adjustment'](row, 1000)
        self.assertEqual(row.payable_amount, 108800)
        namespace['apply_payable_adjustment'](row, 750)
        self.assertEqual(row.payable_amount, 108550)
        self.assertEqual(row.actual_salary, 120000)
        self.assertEqual(row.tds_amount, 12000)
        self.assertEqual(row.professional_tax_amount, 200)
        self.assertEqual(row.adjustment_amount, 750)

    def test_zero_payable_is_allowed(self):
        row = SimpleNamespace(payout_month=date(2026, 11, 1))
        namespace['apply_monthly_salary_breakdown'](self.settings, row, 15000, -15000)
        self.assertEqual(row.payable_amount, 0)

    def test_rejected_adjustment_does_not_mutate_totals(self):
        row = SimpleNamespace(gross_amount=15000, tds_amount=0, adjustment_amount=0, payable_amount=15000)
        with self.assertRaises(TestHttpError):
            namespace['apply_payable_adjustment'](row, -15000.01)
        self.assertEqual(row.adjustment_amount, 0)
        self.assertEqual(row.payable_amount, 15000)

    def test_amount_limits(self):
        for value in (0, 10000000, -10000000, 12.345, float('inf'), float('nan'), 'invalid', None):
            with self.subTest(value=value), self.assertRaises(TestHttpError):
                namespace['validated_adjustment_amount'](value)
        for value in (0.01, -0.01, 9999999.99, -9999999.99, 15000, 144.07):
            with self.subTest(value=value):
                self.assertEqual(namespace['validated_adjustment_amount'](value), value)


if __name__ == '__main__':
    unittest.main()
