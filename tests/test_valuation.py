import unittest
from decimal import Decimal as D

from lothunter.valuation import Fees, Recovery, Scenario, evaluate, maximum_bid, number


class ValuationTests(unittest.TestCase):
    def test_invoice_and_vat_bases(self):
        fees = Fees(D(20), D(5), D(20), D(20), D(10), D(20))
        self.assertEqual(fees.invoice(D(100)), {
            "hammer": D(100), "buyer_fee": D(25), "hammer_vat": D(20),
            "fee_vat": D(5), "inbound": D(10), "inbound_vat": D(2),
        })
        fees = Fees(premium_pct=D(20), fee_vat_pct=D(20), inbound_cost=D(12))
        self.assertEqual(sum(fees.invoice(D(100)).values()), D(136))

    def test_profit_roi_and_max_bid(self):
        # £200 gross, £50 operations, £100 landed = £50 profit on £150 outlay.
        scenario = Scenario(
            Recovery(2, D(100), processing=D(5), labour=D(5),
                     selling_fee_pct=D(10), packing_postage=D(5)),
            Recovery(), Recovery(), 0, margin_pct=D(20),
        )
        result = evaluate(2, Fees(increment=D(5)), scenario, D(100))
        self.assertEqual(result["gross"], D(200))
        self.assertEqual(result["operating_costs"], D(50))
        self.assertEqual(result["profit"], D(50))
        self.assertEqual(result["roi_pct"], D("33.33"))
        self.assertEqual(result["maximum_bid"], D(106))
        self.assertEqual(result["required_profit"], D(40))

    def test_rounding_with_percentage_and_fixed_fee(self):
        # Valid bids £5, £8, £11. £8 + £2.80 fee + £0.56 fee VAT = £11.36.
        fees = Fees(premium_pct=D(10), fixed_fee=D(2), fee_vat_pct=D(20),
                    first_bid=D(5), increment=D(3))
        self.assertEqual(maximum_bid(fees, D("11.36")), D(8))
        self.assertEqual(maximum_bid(fees, D("11.35")), D(5))
        self.assertIsNone(maximum_bid(fees, D(7)))

    def test_percent_rounding_on_invoice_lines(self):
        fees = Fees(premium_pct=D(20), hammer_vat_pct=D(20), fee_vat_pct=D(20))
        self.assertEqual(sum(fees.invoice(D("0.03")).values()), D("0.05"))

    def test_failed_returns_and_clearance(self):
        # 3 normal units at £50, 1 clearance at £10, 1 failed.
        scenario = Scenario(
            Recovery(), Recovery(4, D(50), D(25), D(10), D(4), D(6),
                                 D(10), D(2), D(5)), Recovery(), 1,
            failed_unit_cost=D(3), overhead=D(5), contingency=D(10),
        )
        result = evaluate(5, Fees(), scenario, D(100))
        self.assertEqual(result["gross"], D(160))
        self.assertEqual(result["operating_costs"], D(90))
        self.assertEqual(result["profit"], D(-30))
        self.assertEqual(result["status"], "Unprofitable")
        self.assertEqual(result["maximum_bid"], D(70))

    def test_double_counting_and_incomplete_allocation_rejected(self):
        for parts in (0, 2):
            with self.subTest(parts=parts), self.assertRaisesRegex(ValueError, "Allocate exactly"):
                evaluate(2, Fees(), Scenario(Recovery(1), Recovery(), Recovery(parts), 0))

    def test_unknown_price_and_no_revenue(self):
        result = evaluate(1, Fees(), Scenario(Recovery(), Recovery(), Recovery(), 1))
        self.assertIsNone(result["profit"])
        self.assertIsNone(result["maximum_bid"])
        self.assertEqual(result["status"], "No viable bid")

    def test_corrupt_financial_input(self):
        for value in ("NaN", "Infinity", "-1", ".001", "", "abc", "1e500"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                number(value, "Price")
        with self.assertRaises(ValueError):
            Fees(increment=D(0))
