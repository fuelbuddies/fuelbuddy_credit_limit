# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from fuelbuddy_creditlimit.credit_control import populate_breach_state


class CreditLimit(Document):
	def validate(self):
		# Recompute exposure / overdue / breach from the customer's live transactions on
		# every save -- so a manual change to the credit limit, or a programmatic refresh
		# triggered by a document event, both land in a consistent state.
		populate_breach_state(self)
