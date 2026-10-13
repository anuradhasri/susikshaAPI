import unittest

from app.services.payment_earning_service import calculate_installment


class PaymentEarningCalculationTests(unittest.TestCase):
    def test_split_payments_are_recognized_only_as_each_installment(self):
        installments = [1000, 500, 500]
        results = [calculate_installment(value, 1, 18, "parent", 100) for value in installments]
        self.assertEqual([row["received"] for row in results], installments)
        self.assertEqual(sum(row["received"] for row in results), 2000)
        self.assertEqual(sum(row["gst"] for row in results), 360)
        self.assertEqual(sum(row["after_gst"] for row in results), 1640)
        self.assertEqual(sum(row["therapist"] for row in results), 656)

    def test_group_payment_is_split_before_each_therapist_rule(self):
        lead = calculate_installment(1000, 2, 18, "parent", 100)
        support = calculate_installment(1000, 2, 18, "parent", 40)
        self.assertEqual(lead["received"], 500)
        self.assertEqual(support["received"], 500)
        self.assertEqual(lead["therapist"], 164)
        self.assertEqual(support["therapist"], 65.6)

    def test_installment_components_balance(self):
        result = calculate_installment(500, 2, 18, "parent", 100)
        self.assertEqual(result["received"], 250)
        self.assertEqual(result["gst"], 45)
        self.assertEqual(result["after_gst"], 205)
        self.assertEqual(result["therapist"] + result["organization"], result["after_gst"])


if __name__ == "__main__":
    unittest.main()