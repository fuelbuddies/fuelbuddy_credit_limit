# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""Credit control: per-customer credit limit + overdue enforcement on Delivery Notes.

Model
-----
One ``Credit Limit`` record per Customer is the source of truth. Its ``credit_limit`` is
seeded from the Finance Dossier on submit (and may be adjusted on the record). Every
relevant transaction recomputes the customer's position:

    amount_delivered  = current exposure = outstanding Sales Invoices
                        + value of submitted Delivery Notes not yet invoiced
    amount_remaining  = credit_limit - amount_delivered
    days              = days overdue = today - oldest overdue invoice's due date (0 if none)
    enable_breach     = the customer is overdue (days > 0) OR over the limit
                        (amount_remaining <= 0, only when a limit is configured)
    breach_amount     = overdue outstanding amount when overdue, else the over-limit amount
    date_of_breach    = when the breach was raised (stamped on transition, cleared when it clears)

While ``enable_breach`` is on, Delivery Notes are frozen on save (hard block until cleared --
no auto-expiring window) and an ERPNext Issue is raised.

Cash-advance customers (``Customer.payment_terms == "Cash advance"``) are prepaid and are
handled by the Wallet app instead, so they get no Credit Limit record and are skipped by the
Delivery Note blocker -- the two blockers operate on disjoint customer sets.
"""

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, getdate, now_datetime, nowdate

DOCTYPE = "Credit Limit"

# Issue Type used for the follow-up Issue raised when a Delivery Note is blocked.
ISSUE_TYPE = "Credit Breach"

# Mirrors fuelbuddy_wallet.fuelbuddy_wallet.doctype.wallet.wallet.CASH_ADVANCE_TERMS.
# Kept as a local constant so this app does not depend on the Wallet app being installed.
CASH_ADVANCE_TERMS = "Cash advance"


# -- customer classification -------------------------------------------------


def is_cash_advance(customer):
	"""True when the customer is on Cash-advance terms (prepaid -> Wallet, not credit)."""
	if not customer:
		return False
	return frappe.db.get_value("Customer", customer, "payment_terms") == CASH_ADVANCE_TERMS


def get_or_create_credit_limit(customer):
	"""Return the Credit Limit doc for ``customer`` (creating it if missing).

	Returns ``None`` for cash-advance customers -- they never get a Credit Limit record."""
	if not customer or is_cash_advance(customer):
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


# -- Delivery Note enforcement (R2 overdue + R3 over-limit) ------------------


def check_delivery_note(doc, method=None):
	"""Delivery Note `validate`: freeze the save while the customer is in credit breach.

	Block reasons (only for non-cash-advance customers):
	  * overdue    -- a submitted Sales Invoice is past its due date and still unpaid
	  * over_limit -- the credit limit is exhausted (exposure >= limit)
	On block we raise an ERPNext Issue (enqueued so it survives the throw's rollback)
	and stop the save."""
	customer = doc.get("customer")
	if not customer or is_cash_advance(customer):
		return  # cash-advance customers are enforced by the Wallet blocker

	reason, fig, limit = _block_decision(customer)
	if not reason:
		return

	message = _block_message(customer, reason, fig, limit)

	# Raise the follow-up Issue in a background job: frappe.throw below rolls back this
	# request's transaction, but an immediately-enqueued (not after_commit) job is pushed
	# to the queue regardless and runs in its own transaction.
	frappe.enqueue(
		"fuelbuddy_creditlimit.credit_control.raise_credit_block_issue",
		queue="short",
		customer=customer,
		delivery_note=doc.get("name"),
		reason=reason,
		message=message,
	)
	frappe.throw(message, title=_("Credit Breach"))


def _block_decision(customer):
	"""Return (reason|None, figures, limit). reason is "overdue" (wins), "over_limit", or None."""
	fig = compute_figures(customer)
	limit = flt(frappe.db.get_value(DOCTYPE, {"customer": customer}, "credit_limit"))
	if fig["overdue_days"] > 0:
		return "overdue", fig, limit
	if limit > 0 and fig["exposure"] >= limit:
		return "over_limit", fig, limit
	return None, fig, limit


def _block_message(customer, reason, fig, limit):
	cust_name = frappe.db.get_value("Customer", customer, "customer_name") or customer
	if reason == "overdue":
		invoices = ", ".join(fig["overdue_invoices"][:10])
		return _(
			"Delivery blocked for {0}: payment is overdue by {1} day(s) on invoice(s) {2} "
			"(overdue amount {3}). Clear the overdue invoice(s) before this delivery."
		).format(
			cust_name,
			fig["overdue_days"],
			invoices or "-",
			fmt_money(fig["overdue_amount"]),
		)
	return _(
		"Delivery blocked for {0}: the credit limit is exhausted "
		"(limit {1}, exposure {2}, over by {3})."
	).format(
		cust_name,
		fmt_money(limit),
		fmt_money(fig["exposure"]),
		fmt_money(fig["exposure"] - limit),
	)


def raise_credit_block_issue(customer, delivery_note, reason, message):
	"""Background job: create an ERPNext Issue for a blocked Delivery Note.

	Deduplicated -- if an open credit-block Issue already exists for the customer we skip,
	so repeated save attempts on the same blocked DN don't spawn duplicates."""
	open_issue = frappe.get_all(
		"Issue",
		filters={
			"customer": customer,
			"status": ["not in", ["Closed", "Resolved"]],
			"subject": ["like", "Credit Breach:%"],
		},
		limit=1,
	)
	if open_issue:
		return

	description = message
	if delivery_note:
		description += _("\n\nTriggered while saving Delivery Note {0}.").format(delivery_note)

	issue = frappe.new_doc("Issue")
	issue.subject = f"Credit Breach: {customer}"
	issue.customer = customer
	issue.issue_type = _ensure_issue_type()  # Issue.issue_type is mandatory on this site
	issue.description = description
	issue.flags.ignore_permissions = True
	issue.insert(ignore_permissions=True)
	frappe.db.commit()


def _ensure_issue_type():
	"""Return the 'Credit Breach' Issue Type, creating it once if absent (autoname=Prompt)."""
	if not frappe.db.exists("Issue Type", ISSUE_TYPE):
		it = frappe.new_doc("Issue Type")
		it.name = ISSUE_TYPE
		it.flags.ignore_permissions = True
		it.insert(ignore_permissions=True)
	return ISSUE_TYPE


# -- figure upkeep (document events) -----------------------------------------


def update_on_delivery_note(doc, method=None):
	"""Delivery Note on_submit / on_cancel: refresh the customer's credit position.

	This is the "every DN punch changes data on the Credit Limit doctype" behaviour."""
	customer = doc.get("customer")
	if customer and not is_cash_advance(customer):
		recompute(customer)


def refresh_on_invoice(doc, method=None):
	"""Sales Invoice on_submit / on_cancel: refresh the customer's credit position."""
	customer = doc.get("customer")
	if customer:
		recompute(customer)


def refresh_on_payment_entry(doc, method=None):
	"""Payment Entry on_submit / on_cancel: a payment frees credit / can clear overdue."""
	if doc.get("party_type") == "Customer" and doc.get("party"):
		recompute(doc.party)


# -- Finance Dossier sync (R1) -----------------------------------------------


def sync_limit_from_finance_dossier(doc, method=None):
	"""Finance Dossier on_submit: push its Credit Insurance Limit onto the Customer.

	Runs after the Finance Dossier controller's on_submit has created the Sales Order
	(and, for Lead-based quotations, the Customer), so the customer can be resolved via
	the Sales Order raised from the dossier's quotation."""
	customer = _resolve_customer_from_fd(doc)
	if not customer or is_cash_advance(customer):
		return
	credit = get_or_create_credit_limit(customer)
	if not credit:
		return
	credit.credit_limit = flt(doc.get("credit_insurance_limit"))
	credit.flags.ignore_permissions = True
	credit.save(ignore_permissions=True)  # validate -> populate_breach_state


def _resolve_customer_from_fd(doc):
	"""Customer behind a Finance Dossier, via the Sales Order raised from its quotation."""
	quotation = doc.id if doc.get("finance_dossier_from") == "Quotation" else None
	if not quotation:
		return None
	customer = frappe.db.get_value(
		"Sales Order", {"custom_quotation": quotation, "docstatus": ["<", 2]}, "customer"
	)
	if customer:
		return customer
	# Fallback: a Quotation raised directly against an existing Customer.
	qto, party = frappe.db.get_value("Quotation", quotation, ["quotation_to", "party_name"]) or (
		None,
		None,
	)
	return party if qto == "Customer" else None


# -- Customer lifecycle + scheduler ------------------------------------------


def create_credit_for_customer(doc, method=None):
	"""Customer after_insert: create a Credit Limit record for every non-cash-advance customer."""
	if doc.get("payment_terms") == CASH_ADVANCE_TERMS:
		return
	get_or_create_credit_limit(doc.name)


def refresh_overdue_daily():
	"""Daily scheduler: recompute every Credit Limit record so invoices that crossed their
	due date today flip the breach (a due date passing has no document event to hook)."""
	for row in frappe.get_all(DOCTYPE, fields=["customer"]):
		try:
			recompute(row.customer)
		except Exception:
			frappe.log_error(
				title="Credit Limit daily refresh failed",
				message=f"{row.customer}: {frappe.get_traceback()}",
			)
