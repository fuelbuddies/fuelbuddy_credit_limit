# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

import datetime

import frappe
from erpnext import get_default_company
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from fuelbuddy_creditlimit.credit_control import mark_dirty


class CreditLimit(Document):
	def validate(self):
		validate_covers(self)

	def on_update(self):
		# the engine's own insert is already being computed; any other save (e.g. covers edited) recomputes
		if not self.flags.from_recompute:
			mark_dirty(self.customer)


def validate_covers(doc):
	currency = frappe.get_cached_value("Company", get_default_company(), "default_currency")
	approved = []
	for row in doc.covers:
		row.currency = row.currency or currency
		if row.valid_from and row.valid_to and getdate(row.valid_to) < getdate(row.valid_from):
			frappe.throw(_("Row {0}: Valid To cannot be before Valid From").format(row.idx))
		if row.status == "Approved":
			if flt(row.approved_limit) <= 0:
				frappe.throw(_("Row {0}: an Approved cover needs an Approved Limit above 0").format(row.idx))
			if row.valid_from:
				approved.append(row)

	def end(row):
		return getdate(row.valid_to) if row.valid_to else datetime.date.max

	for i, a in enumerate(approved):
		for b in approved[i + 1 :]:
			if a.cover_type != b.cover_type:
				continue
			if getdate(a.valid_from) <= end(b) and getdate(b.valid_from) <= end(a):
				frappe.throw(
					_("Approved {0} covers overlap: rows {1} and {2}").format(a.cover_type, a.idx, b.idx)
				)
