# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""Create + populate a Credit record for every existing non-cash-advance Customer.

Idempotent: refresh_customer_credit creates the record if missing and skips cash-advance
customers, so re-running is safe."""

import frappe

from fuelbuddy_creditlimit.credit_control import refresh_customer_credit


def execute():
	customers = frappe.get_all("Customer", filters={"disabled": 0}, pluck="name")
	for customer in customers:
		try:
			refresh_customer_credit(customer)
		except Exception:
			frappe.log_error(
				title="Credit backfill failed",
				message=f"{customer}: {frappe.get_traceback()}",
			)
