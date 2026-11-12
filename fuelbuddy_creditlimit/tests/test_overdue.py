# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""overdue(): FIFO credit, day-N boundary, no minimum amount. Pure, so plain unittest without a site."""

import datetime
import unittest

from fuelbuddy_creditlimit.overdue import OpenInvoice, overdue

D = datetime.date
TODAY = D(2026, 11, 10)


class TestOverdue(unittest.TestCase):
	def test_credit_pays_oldest_first(self):
		# A is paid off and B partly: B's 15K is 40 days past due, C is not yet late
		invoices = [
			OpenInvoice("C", D(2026, 11, 1), 10_000),
			OpenInvoice("A", D(2026, 9, 1), 30_000),
			OpenInvoice("B", D(2026, 10, 1), 20_000),
		]
		od = overdue(invoices, 35_000, TODAY, 15)
		self.assertEqual(od.amount, 15_000)
		self.assertEqual(od.days, 40)
		self.assertEqual(od.invoices, (("B", D(2026, 10, 1), 15_000),))

	def test_stops_on_day_n(self):
		self.assertEqual(overdue([OpenInvoice("A", D(2026, 10, 26), 500)], 0, TODAY, 15).amount, 500)

	def test_day_before_n_is_not_overdue_but_days_still_count(self):
		od = overdue([OpenInvoice("A", D(2026, 10, 27), 500)], 0, TODAY, 15)
		self.assertEqual(od.amount, 0)
		self.assertEqual(od.days, 14)

	def test_no_minimum_amount(self):
		self.assertEqual(overdue([OpenInvoice("A", D(2026, 9, 1), 0.01)], 0, TODAY, 15).amount, 0.01)

	def test_credit_covering_everything(self):
		od = overdue([OpenInvoice("A", D(2026, 9, 1), 100)], 250, TODAY, 15)
		self.assertEqual((od.amount, od.days, od.invoices), (0, 0, ()))

	def test_not_yet_due_reports_zero_days(self):
		self.assertEqual(overdue([OpenInvoice("A", D(2026, 12, 1), 100)], 0, TODAY, 15).days, 0)

	def test_same_due_date_ordered_by_name(self):
		invoices = [OpenInvoice("B", D(2026, 9, 1), 100), OpenInvoice("A", D(2026, 9, 1), 100)]
		self.assertEqual(overdue(invoices, 100, TODAY, 15).invoices, (("B", D(2026, 9, 1), 100),))

	def test_negative_credit_is_ignored(self):
		self.assertEqual(overdue([OpenInvoice("A", D(2026, 9, 1), 100)], -50, TODAY, 15).amount, 100)


if __name__ == "__main__":
	unittest.main()
