# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""grant(), litres_for() and so_line_litres(): the arithmetic of dispense holds. Plain unittest."""

import math
import unittest

from fuelbuddy_creditlimit.dispense_rules import grant, litres_for, so_line_litres


class TestGrant(unittest.TestCase):
	def test_wanted_when_every_pool_has_more(self):
		self.assertEqual(grant(300, {"sales_order": 5000, "wallet": 400}), (300, "wanted"))

	def test_lowest_pool_sets_the_grant(self):
		self.assertEqual(grant(300, {"sales_order": 5000, "wallet": 120.7, "credit": None}), (120, "wallet"))

	def test_pools_that_do_not_apply_are_skipped(self):
		self.assertEqual(grant(300, {"wallet": None, "credit": 250, "sales_order": 280}), (250, "credit"))

	def test_whole_litres_rounded_down(self):
		self.assertEqual(grant(300, {"sales_order": 99.999}), (99, "sales_order"))

	def test_float_noise_does_not_lose_a_litre(self):
		self.assertEqual(grant(300, {"sales_order": 0.1 * 3 * 1000 / 3}), (100, "sales_order"))

	def test_empty_or_negative_pool_gives_nothing(self):
		self.assertEqual(grant(300, {"wallet": -50, "sales_order": 900}), (0, "wallet"))
		self.assertEqual(grant(300, {"sales_order": 0}), (0, "sales_order"))

	def test_tie_goes_to_the_first_pool(self):
		self.assertEqual(grant(300, {"sales_order": 100, "wallet": 100}), (100, "sales_order"))

	def test_nothing_wanted_reports_the_tightest_pool(self):
		self.assertEqual(grant(math.inf, {"sales_order": 800, "credit": 650.5}), (650, "credit"))

	def test_nothing_wanted_and_no_pool_stays_unbounded(self):
		litres, by = grant(math.inf, {"wallet": None})
		self.assertTrue(math.isinf(litres))
		self.assertEqual(by, "wanted")


class TestLitresFor(unittest.TestCase):
	def test_amount_over_rate(self):
		self.assertAlmostEqual(litres_for(1000, 2.5), 400)

	def test_no_rate_buys_nothing(self):
		self.assertEqual(litres_for(1000, 0), 0.0)
		self.assertEqual(litres_for(1000, None), 0.0)

	def test_negative_amount_stays_negative(self):
		self.assertAlmostEqual(litres_for(-250, 2.5), -100)


class TestSalesOrderLineLitres(unittest.TestCase):
	def test_ordered_minus_delivered_minus_drafts_plus_returned(self):
		line = {
			"qty": 10000,
			"delivered_qty": 6000,
			"custom_delivery_note_qty_in_draft": 500,
			"returned_qty": 200,
			"conversion_factor": 1,
		}
		self.assertEqual(so_line_litres(line), 3700)

	def test_conversion_factor_turns_the_line_unit_into_litres(self):
		self.assertEqual(so_line_litres({"qty": 10, "delivered_qty": 4, "conversion_factor": 1000}), 6000)

	def test_missing_fields_count_as_zero_and_factor_as_one(self):
		self.assertEqual(so_line_litres({"qty": 50}), 50)

	def test_over_delivered_line_is_negative(self):
		self.assertEqual(so_line_litres({"qty": 100, "delivered_qty": 120, "conversion_factor": 1}), -20)

	def test_reads_attributes_too(self):
		class Row:
			qty, delivered_qty, custom_delivery_note_qty_in_draft, returned_qty, conversion_factor = (
				20,
				5,
				5,
				0,
				2,
			)

		self.assertEqual(so_line_litres(Row()), 20)


if __name__ == "__main__":
	unittest.main()
