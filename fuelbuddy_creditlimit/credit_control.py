# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""Credit control: per-customer limit, exposure and stop flags for the platform.
ERP never refuses a Delivery Note."""

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, now_datetime
from frappe.utils.background_jobs import is_job_enqueued

from fuelbuddy_creditlimit.credit_engine import compute_credit, is_in_scope

DOCTYPE = "Credit Limit"
DIRTY_KEY = "credit:dirty"
MONEY = ("operating_limit", "billed_amount", "unbilled_amount", "exposure", "amount_remaining")
COMPARE = (*MONEY, "credit_status", "stop_reasons", "days")

# Master switch. The shared "Fuelbuddy Settings" single (hosted in fuelbuddy_crm,
# read by all FuelBuddy apps) gates this whole feature via its "Enable Credit Limit"
# flag. When off (the default), every document-event/scheduler hook below no-ops.
FB_SETTINGS_DOCTYPE = "Fuelbuddy Settings"


def _credit_limit_enabled():
	"""True when the Credit Limit feature is enabled in Fuelbuddy Settings (default off)."""
	return bool(frappe.db.get_single_value(FB_SETTINGS_DOCTYPE, "enable_credit_limit"))


def mark_dirty(*customers):
	"""Queue the customers for the minute drain once this transaction commits."""
	customers = {c for c in customers if c}
	if not customers or not _credit_limit_enabled():
		return
	frappe.db.after_commit.add(lambda: frappe.cache.sadd(DIRTY_KEY, *customers))


def drain_dirty():
	"""Minute cron: one deduplicated recompute job per dirty customer.
	A customer whose job is still queued or running stays dirty for the next minute."""
	if not _credit_limit_enabled():
		return
	for member in frappe.cache.smembers(DIRTY_KEY):
		customer = member.decode()
		if is_job_enqueued(f"credit:{customer}"):
			continue
		frappe.cache.srem(DIRTY_KEY, customer)
		frappe.enqueue(
			"fuelbuddy_creditlimit.credit_control.recompute_credit",
			queue="short",
			job_id=f"credit:{customer}",
			deduplicate=True,
			customer=customer,
		)


def sweep_credit():
	"""15-minute cron: re-mark every enabled customer (a due date passing or an exception expiring has no event),
	and every record still in scope, so a disabled customer's record drops out once."""
	if not _credit_limit_enabled():
		return
	customers = frappe.get_all("Customer", filters={"disabled": 0}, pluck="name")
	customers += frappe.get_all(DOCTYPE, filters={"in_scope": 1}, pluck="customer")
	if customers:
		frappe.cache.sadd(DIRTY_KEY, *customers)


def recompute_credit(customer):
	"""Store compute_credit on the customer's record, created when in scope; status changes go to the Version log."""
	if not _credit_limit_enabled():
		return None
	frappe.db.commit()
	# before any read: a snapshot must never claim data committed after it
	as_of = now_datetime()
	name = frappe.db.get_value(DOCTYPE, {"customer": customer})
	if not name:
		if not is_in_scope(customer):
			return None
		name = frappe.get_doc({"doctype": DOCTYPE, "customer": customer}).insert(ignore_permissions=True).name
	doc = frappe.get_doc(DOCTYPE, name, for_update=True)
	values = compute_credit(customer, as_of)
	values.last_refreshed = as_of
	values.stopped_since = (doc.stopped_since or as_of) if values.credit_status == "Stopped" else None
	changed = [
		[f, doc.get(f), values[f]] for f in ("credit_status", "stop_reasons") if cstr(doc.get(f)) != values[f]
	]
	doc.db_set(values, update_modified=False)
	if changed:
		frappe.get_doc(
			{
				"doctype": "Version",
				"ref_doctype": DOCTYPE,
				"docname": name,
				"data": frappe.as_json({"changed": changed}),
			}
		).insert(ignore_permissions=True)
	return name


def _typed(field, value):
	return flt(value) if field in MONEY else cint(value) if field == "days" else cstr(value)


@frappe.whitelist(methods=["POST"])
def refresh_customer_credit(customer):
	"""The form's 'Correct Now': recompute and store the customer's figures."""
	frappe.only_for(("Credit Controller", "System Manager"))
	return recompute_credit(customer)


@frappe.whitelist()
def check_customer_credit(customer):
	"""Dry-run for the form's reconcile flow: stored figures vs compute_credit, no writes."""
	frappe.only_for(("Credit Controller", "System Manager"))
	row = frappe.db.get_value(DOCTYPE, {"customer": customer}, COMPARE, as_dict=True)
	if not row:
		frappe.throw(_("No Credit Limit record for {0}").format(customer))
	live = compute_credit(customer)
	stored = {f: _typed(f, row[f]) for f in COMPARE}
	expected = {f: _typed(f, live[f]) for f in COMPARE}
	in_sync = all(
		abs(stored[f] - expected[f]) < 0.005 if f in MONEY else stored[f] == expected[f] for f in COMPARE
	)
	return {"stored": stored, "expected": expected, "in_sync": in_sync}


# -- mark dirty on money events (one submit-side and one cancel-side handler per doctype) --


def _customers(doc):
	if doc.doctype == "Delivery Note" and doc.get("custom_invoiced_item_id"):
		return []  # the platform counts its own deliveries
	if doc.doctype in ("Delivery Note", "Sales Invoice"):
		return [doc.customer]
	if doc.doctype == "Payment Entry":
		return [doc.party] if doc.party_type == "Customer" else []
	if doc.doctype == "Journal Entry":
		return [row.party for row in doc.accounts if row.party_type == "Customer"]
	if doc.doctype == "Customer":
		return [doc.name]
	return []


def mark_on_delivery_note_save(doc, method=None):
	mark_dirty(*_customers(doc))


def mark_on_delivery_note_cancel(doc, method=None):
	mark_dirty(*_customers(doc))


def mark_on_invoice_submit(doc, method=None):
	mark_dirty(*_customers(doc))


def mark_on_invoice_cancel(doc, method=None):
	mark_dirty(*_customers(doc))


def mark_on_payment_entry_submit(doc, method=None):
	mark_dirty(*_customers(doc))


def mark_on_payment_entry_cancel(doc, method=None):
	mark_dirty(*_customers(doc))


def mark_on_journal_entry_submit(doc, method=None):
	mark_dirty(*_customers(doc))


def mark_on_journal_entry_cancel(doc, method=None):
	mark_dirty(*_customers(doc))


def mark_on_customer_insert(doc, method=None):
	mark_dirty(*_customers(doc))


def validate_credit_settings(doc, method=None):
	"""Fuelbuddy Settings validate: the overdue grace is at least 15 days (0 means the default)."""
	grace = cint(doc.get("custom_credit_overdue_grace_days"))
	if grace and grace < 15:
		frappe.throw(_("Overdue Grace Days must be at least 15."))
