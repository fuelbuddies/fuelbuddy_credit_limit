# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""Credit control: per-customer limit, exposure and stop flags for the platform.
ERP never refuses a Delivery Note."""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime, nowdate

DOCTYPE = "Credit Limit"

# Master switch. The shared "Fuelbuddy Settings" single (hosted in fuelbuddy_crm,
# read by all FuelBuddy apps) gates this whole feature via its "Enable Credit Limit"
# flag. When off (the default), every document-event/scheduler hook below no-ops.
FB_SETTINGS_DOCTYPE = "Fuelbuddy Settings"


def _credit_limit_enabled():
	"""True when the Credit Limit feature is enabled in Fuelbuddy Settings (default off)."""
	return bool(frappe.db.get_single_value(FB_SETTINGS_DOCTYPE, "enable_credit_limit"))


def get_or_create_credit_limit(customer):
	"""Return the Credit Limit doc for ``customer`` (creating it if missing)."""
	if not customer:
		return None
	name = frappe.db.get_value(DOCTYPE, {"customer": customer}, "name")
	if name:
		return frappe.get_doc(DOCTYPE, name)
	doc = frappe.new_doc(DOCTYPE)
	doc.customer = customer
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return doc


# -- computation -------------------------------------------------------------


def compute_figures(customer):
	"""Read-only snapshot of a customer's exposure and overdue position."""
	today = getdate(nowdate())

	invoices = frappe.get_all(
		"Sales Invoice",
		filters={"customer": customer, "docstatus": 1, "outstanding_amount": [">", 0]},
		fields=["name", "outstanding_amount", "due_date"],
	)
	total_outstanding = sum(flt(i.outstanding_amount) for i in invoices)
	overdue = [i for i in invoices if i.due_date and getdate(i.due_date) < today]
	overdue_amount = sum(flt(i.outstanding_amount) for i in overdue)
	oldest = min((getdate(i.due_date) for i in overdue), default=None)
	overdue_days = (today - oldest).days if oldest else 0

	# Goods delivered but not yet invoiced still consume credit. per_billed is the
	# percentage of the Delivery Note already billed, so (100 - per_billed)% of its
	# value is the un-invoiced (and therefore not-yet-outstanding) portion.
	dns = frappe.get_all(
		"Delivery Note",
		filters={"customer": customer, "docstatus": 1, "per_billed": ["<", 100]},
		fields=["grand_total", "per_billed"],
	)
	delivered_not_invoiced = sum(
		flt(d.grand_total) * (100.0 - flt(d.per_billed)) / 100.0 for d in dns
	)

	return {
		"exposure": total_outstanding + delivered_not_invoiced,
		"total_outstanding": total_outstanding,
		"overdue_amount": overdue_amount,
		"overdue_days": overdue_days,
		"overdue_invoices": [i.name for i in overdue],
	}


def populate_breach_state(doc):
	"""Fill exposure / overdue / breach fields on a Credit Limit doc from live transactions.

	Called from CreditLimit.validate, so it runs on every save (manual or programmatic)."""
	fig = compute_figures(doc.customer)
	limit = flt(doc.credit_limit)
	exposure = fig["exposure"]

	doc.amount_delivered = exposure
	doc.amount_remaining = limit - exposure
	doc.days = fig["overdue_days"]

	over_limit = limit > 0 and exposure >= limit
	breach = fig["overdue_days"] > 0 or over_limit

	if fig["overdue_days"] > 0:
		doc.breach_amount = fig["overdue_amount"]
	elif over_limit:
		doc.breach_amount = exposure - limit
	else:
		doc.breach_amount = 0.0

	# Stamp date_of_breach when the breach is newly raised; clear it when it clears.
	prior = frappe.db.get_value(DOCTYPE, doc.name, "enable_breach") if not doc.is_new() else 0
	if breach and not prior:
		doc.date_of_breach = now_datetime()
	elif not breach:
		doc.date_of_breach = None
	doc.enable_breach = 1 if breach else 0

	doc.last_refreshed = now_datetime()


def recompute(customer):
	"""Ensure the customer's Credit Limit record exists and is refreshed from transactions."""
	doc = get_or_create_credit_limit(customer)
	if not doc:
		return None
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)  # validate -> populate_breach_state
	return doc.name


@frappe.whitelist()
def refresh_customer_credit(customer):
	"""Whitelisted entry point for the form's 'Refresh from Transactions' button."""
	return recompute(customer)


@frappe.whitelist()
def check_customer_credit(customer):
	"""Dry-run for the form's reconcile flow: stored Credit Limit amounts vs the
	live figures, no writes. Safety net for bulk submit / cancel flows whose
	events raced or were missed; `recompute` (the correct step) is total and
	idempotent."""
	name = frappe.db.get_value(DOCTYPE, {"customer": customer}, "name")
	if not name:
		frappe.throw(_("No Credit Limit record for {0}").format(customer))
	row = frappe.db.get_value(
		DOCTYPE,
		name,
		["credit_limit", "amount_delivered", "amount_remaining", "days"],
		as_dict=True,
	)
	fig = compute_figures(customer)
	limit = flt(row.credit_limit)
	expected = {
		"amount_delivered": fig["exposure"],
		"amount_remaining": limit - fig["exposure"],
		"days": fig["overdue_days"],
	}
	stored = {
		"amount_delivered": flt(row.amount_delivered),
		"amount_remaining": flt(row.amount_remaining),
		"days": cint(row.days),
	}
	in_sync = (
		abs(stored["amount_delivered"] - expected["amount_delivered"]) < 0.005
		and abs(stored["amount_remaining"] - expected["amount_remaining"]) < 0.005
		and stored["days"] == cint(expected["days"])
	)
	return {"stored": stored, "expected": expected, "in_sync": in_sync}


# -- figure upkeep (document events) -----------------------------------------


# Per team convention: one submit-side and one cancel-side handler per doctype.


def update_on_delivery_note_submit(doc, method=None):
	"""Delivery Note on_submit: refresh the customer's credit position.

	This is the "every DN punch changes data on the Credit Limit doctype" behaviour."""
	if not _credit_limit_enabled():
		return
	customer = doc.get("customer")
	if customer:
		recompute(customer)


def update_on_delivery_note_cancel(doc, method=None):
	"""Delivery Note on_cancel: the DN leaves the exposure; refresh (bulk cancel included)."""
	if not _credit_limit_enabled():
		return
	customer = doc.get("customer")
	if customer:
		recompute(customer)


def refresh_on_invoice_submit(doc, method=None):
	"""Sales Invoice on_submit: refresh the customer's credit position."""
	if not _credit_limit_enabled():
		return
	customer = doc.get("customer")
	if customer:
		recompute(customer)


def refresh_on_invoice_cancel(doc, method=None):
	"""Sales Invoice on_cancel: outstanding drops with the invoice; refresh."""
	if not _credit_limit_enabled():
		return
	customer = doc.get("customer")
	if customer:
		recompute(customer)


def refresh_on_payment_entry_submit(doc, method=None):
	"""Payment Entry on_submit: a payment frees credit / can clear overdue."""
	if not _credit_limit_enabled():
		return
	if doc.get("party_type") == "Customer" and doc.get("party"):
		recompute(doc.party)


def refresh_on_payment_entry_cancel(doc, method=None):
	"""Payment Entry on_cancel: the payment's allocation is reversed, outstanding
	rises again; refresh so exposure/overdue reflect it (bulk cancel included)."""
	if not _credit_limit_enabled():
		return
	if doc.get("party_type") == "Customer" and doc.get("party"):
		recompute(doc.party)


# -- Customer lifecycle + scheduler ------------------------------------------


def create_credit_for_customer(doc, method=None):
	"""Customer after_insert: create a Credit Limit record for the customer."""
	if not _credit_limit_enabled():
		return
	get_or_create_credit_limit(doc.name)


def refresh_overdue_daily():
	"""Daily scheduler: recompute every Credit Limit record so invoices that crossed their
	due date today flip the breach (a due date passing has no document event to hook)."""
	if not _credit_limit_enabled():
		return
	for row in frappe.get_all(DOCTYPE, fields=["customer"]):
		try:
			recompute(row.customer)
		except Exception:
			frappe.log_error(
				title="Credit Limit daily refresh failed",
				message=f"{row.customer}: {frappe.get_traceback()}",
			)
