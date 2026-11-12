# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""unbilled_qty() and unbilled_share(): per item, litres delivered minus litres invoiced over all time are the
newest deliveries. Plain unittest."""

import unittest

from fuelbuddy_creditlimit.unbilled import Delivery, unbilled_qty, unbilled_share


class TestUnbilledQty(unittest.TestCase):
	def test_delivered_minus_invoiced_per_item(self):
		self.assertEqual(unbilled_qty({"D": 1000, "P": 50}, {"D": 700, "P": 50}), {"D": 300})

	def test_nothing_invoiced_is_all_unbilled(self):
		self.assertEqual(unbilled_qty({"D": 1000}, {}), {"D": 1000})

	def test_invoiced_more_than_delivered_leaves_nothing_unbilled(self):
		self.assertEqual(unbilled_qty({"D": 100}, {"D": 250}), {})

	def test_credit_notes_reduce_invoiced(self):
		self.assertEqual(unbilled_qty({"D": 100}, {"D": 100 - 30}), {"D": 30})

	def test_invoiced_item_never_delivered_is_ignored(self):
		self.assertEqual(unbilled_qty({"D": 100}, {"D": 100, "X": 5}), {})

	def test_a_small_difference_still_counts(self):
		# no floor: rounding differences are finance's to clear
		self.assertAlmostEqual(unbilled_qty({"D": 7495.83}, {"D": 7495.77})["D"], 0.06, places=6)


class TestUnbilledShare(unittest.TestCase):
	def test_unbilled_litres_are_the_newest_deliveries(self):
		deliveries = [
			Delivery("d1", "D", (1,), 100),
			Delivery("d3", "D", (3,), 100),
			Delivery("d2", "D", (2,), 100),
		]
		self.assertEqual(unbilled_share(deliveries, {"D": 150}), {"d3": 1.0, "d2": 0.5})

	def test_an_old_delivery_never_invoiced_stays_unbilled(self):
		deliveries = [Delivery("old", "D", (2020, 1), 100), Delivery("new", "D", (2026, 11), 100)]
		self.assertEqual(unbilled_share(deliveries, {"D": 200}), {"old": 1.0, "new": 1.0})

	def test_items_are_netted_separately(self):
		deliveries = [Delivery("a", "D", (1,), 100), Delivery("b", "P", (1,), 100)]
		self.assertEqual(unbilled_share(deliveries, {"P": 100}), {"b": 1.0})

	def test_nothing_unbilled(self):
		self.assertEqual(unbilled_share([Delivery("a", "D", (1,), 100)], {}), {})

	def test_zero_quantity_delivery_is_skipped(self):
		deliveries = [Delivery("z", "D", (2,), 0), Delivery("a", "D", (1,), 40)]
		self.assertEqual(unbilled_share(deliveries, {"D": 40}), {"a": 1.0})


if __name__ == "__main__":
	unittest.main()
