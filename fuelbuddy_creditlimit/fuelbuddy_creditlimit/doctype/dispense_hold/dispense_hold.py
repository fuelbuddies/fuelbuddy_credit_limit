# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class DispenseHold(Document):
	"""Written only by fuelbuddy_creditlimit.dispense_hold (the platform API and the Delivery Note hook)."""
