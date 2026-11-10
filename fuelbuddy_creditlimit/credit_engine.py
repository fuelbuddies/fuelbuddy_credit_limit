# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""One customer's credit figures: limits, exposure, overdue, live exceptions and stop status. Never writes."""

import datetime

import frappe
from erpnext import get_default_company
from erpnext.setup.utils import get_exchange_rate
from frappe.utils import cint, flt, getdate, now_datetime

from fuelbuddy_creditlimit.overdue import OpenInvoice, overdue


def credit_settings():
	"""The credit fields of Fuelbuddy Settings. Blank or 0 means the default: Singles get no defaults on migrate."""
	s = frappe.db.get_singles_dict("Fuelbuddy Settings")
	since = getdate(s.custom_credit_unbilled_from) if s.custom_credit_unbilled_from else None
	return frappe._dict(
		credit_insured_factor=flt(s.custom_credit_insured_factor) or 80,
		credit_warning_percent=flt(s.custom_credit_warning_percent) or 80,
		credit_overdue_grace_days=cint(s.custom_credit_overdue_grace_days) or 15,
		# before 2000 = unset (Frappe reads a NULL Date as year 1); else U would count every DN since go-live
		credit_unbilled_from=since if since and since.year >= 2000 else datetime.date(2026, 6, 1),
		credit_exception_max_days=cint(s.custom_credit_exception_max_days) or 7,
	)


def _has_wallet(customer):
	return bool(frappe.db.exists("Wallet", {"customer": customer, "payment_type": "Wallet"}))


def is_in_scope(customer):
	"""Credit customers: enabled, and no Wallet or a Wallet plus a day-term invoice."""
	if frappe.db.get_value("Customer", customer, "disabled"):
		return False
	if not _has_wallet(customer):
		return True
	return bool(
		frappe.db.sql(
			"""select 1 from `tabSales Invoice` si
			where si.customer = %s and si.docstatus = 1 and si.is_return = 0
				and exists (select 1 from `tabPayment Terms Template Detail` ptd
					where ptd.parent = si.payment_terms_template and ptd.credit_days > 0)
			limit 1""",
			customer,
		)
	)


def _limits(customer, d, currency):
	"""Totals per cover type of the Approved covers active on d, in company currency; a type with none is absent."""
	limits = {}
	record = frappe.db.get_value("Credit Limit", {"customer": customer})
	if not record:
		return limits
	for row in frappe.get_all(
		"Credit Cover",
		filters={"parent": record, "parenttype": "Credit Limit", "status": "Approved"},
		fields=["cover_type", "approved_limit", "currency", "valid_from", "valid_to"],
	):
		if getdate(row.valid_from) <= d <= getdate(row.valid_to or d):
			rate = get_exchange_rate(row.currency or currency, currency, d)  # 1 when the currencies match
			limits[row.cover_type] = limits.get(row.cover_type, 0.0) + flt(row.approved_limit) * flt(rate)
	return limits


def _billed(customer, d):
	return flt(
		frappe.db.sql(
			"""select coalesce(sum(amount), 0) from `tabPayment Ledger Entry`
			where party_type = 'Customer' and party = %s and delinked = 0
				and account_type = 'Receivable' and posting_date <= %s""",
			(customer, d),
		)[0][0]
	)


def _open_invoices(customer, d):
	"""(open invoices, unmatched credit) from per-voucher ledger balances in company currency, whatever the
	invoice currency; an amended invoice keeps its root's dates."""
	balances = frappe.db.sql(
		"""select against_voucher_type, against_voucher_no, sum(amount) balance from `tabPayment Ledger Entry`
		where party_type = 'Customer' and party = %s and delinked = 0
			and account_type = 'Receivable' and posting_date <= %s
		group by against_voucher_type, against_voucher_no""",
		(customer, d),
		as_dict=True,
	)
	credit = -sum(flt(b.balance) for b in balances if b.balance < 0)
	open_si = {
		b.against_voucher_no: flt(b.balance)
		for b in balances
		if b.against_voucher_type == "Sales Invoice" and b.balance > 0
	}
	invoices = []
	for si in frappe.get_all(
		"Sales Invoice",
		# opening invoices stay out until finance confirms they count (IDEV-3419)
		filters={"name": ("in", list(open_si)), "is_opening": ("!=", "Yes")},
		fields=["name", "posting_date", "due_date", "amended_from"],
	):
		root = si
		while root.amended_from:
			root = frappe.db.get_value(
				"Sales Invoice", root.amended_from, ["posting_date", "due_date", "amended_from"], as_dict=True
			)
		due = max(getdate(root.due_date or root.posting_date), getdate(root.posting_date))
		invoices.append(OpenInvoice(si.name, due, open_si[si.name]))
	return invoices, credit


def unbilled_by_dn(customer, as_of, since):
	"""{Delivery Note: value incl. VAT, company currency} for DNs no invoice billed, one per delivery."""
	# imported here so a wallet build without the helper fails only this job, never a DN/SI/PE/JE hook
	from fuelbuddy_wallet.fuelbuddy_wallet.doctype.wallet.wallet import _billable_rows

	d = getdate(as_of)
	# billed iff a covering invoice was submitted (first ledger row) after the DN; per-date so MariaDB caches it
	rows = frappe.db.sql(
		"""select dn.name, dn.custom_invoiced_item_id iid, dn.docstatus, dn.creation, dn.base_grand_total, dn.net_total,
			dni.so_detail, dni.against_sales_order, dn.posting_date, dni.item_code, dni.qty, dni.amount
		from `tabDelivery Note` dn join `tabDelivery Note Item` dni on dni.parent = dn.name
		where dn.customer = %(c)s and dn.docstatus < 2 and dn.is_return = 0
			and dn.posting_date between %(since)s and %(d)s
			and dn.creation >= coalesce((select max((select min(p.creation) from `tabPayment Ledger Entry` p
					where p.voucher_type = 'Sales Invoice' and p.voucher_no = si.name))
				from `tabSales Invoice` si where si.customer = dn.customer and si.docstatus = 1 and si.is_return = 0
					and si.posting_date <= %(d)s
					and dn.posting_date between si.custom_dn_from_date and si.custom_dn_to_date), '2000-01-01')""",
		{"c": customer, "since": since, "d": d},
		as_dict=True,
	)
	# two DNs for one platform delivery: keep the submitted one, then the newest
	best = {}
	for r in rows:
		key = r.iid or r.name
		if key not in best or (r.docstatus, r.creation) > (best[key].docstatus, best[key].creation):
			best[key] = r
	kept = {r.name for r in best.values()}
	rows = [r for r in rows if r.name in kept]
	values = _billable_rows(
		customer,
		[(r.so_detail, r.against_sales_order, r.posting_date, r.item_code, r.qty, r.amount) for r in rows],
		d,
	)
	out = {}
	for r, (value, _fm) in zip(rows, values, strict=True):
		net = flt(r.net_total)
		out[r.name] = out.get(r.name, 0.0) + value * (flt(r.base_grand_total) / net if net else 1.0)
	return out


def compute_credit(customer, as_of=None, settings=None):
	"""Limits, exposure, overdue and stop status of one customer as of a moment, live exceptions applied."""
	as_of = as_of or now_datetime()
	d = getdate(as_of)
	s = settings or credit_settings()
	currency = frappe.get_cached_value("Company", get_default_company(), "default_currency")

	limits = _limits(customer, d, currency)
	insured, committee = limits.get("Insurance", 0.0), limits.get("Committee", 0.0)
	share = insured * flt(s.credit_insured_factor) / 100
	# both covers active: min, even when a 0 exchange rate zeroes one of them
	limit = min(share, committee) if "Insurance" in limits and "Committee" in limits else share or committee

	billed = _billed(customer, d)
	unbilled = sum(unbilled_by_dn(customer, as_of, s.credit_unbilled_from).values())
	exposure = billed + unbilled
	od = overdue(*_open_invoices(customer, d), d, s.credit_overdue_grace_days)

	live = frappe.get_all(
		"Credit Exception",
		filters={
			"customer": customer,
			"docstatus": 1,
			"valid_from": ("<=", as_of),
			"expires_at": (">", as_of),
		},
		fields=["cap", "lift_overdue", "lift_no_limit", "expires_at"],
	)
	cap = sum(flt(x.cap) for x in live)
	lifts = [
		r for r, f in (("no_limit", "lift_no_limit"), ("overdue", "lift_overdue")) if any(x[f] for x in live)
	]

	in_scope = is_in_scope(customer)
	reasons = []
	if limit <= 0 and "no_limit" not in lifts:
		reasons.append("no_limit")
	elif exposure >= limit + cap:
		reasons.append("over_limit")
	if od.amount > 0 and "overdue" not in lifts and not _has_wallet(customer):
		reasons.append("overdue")
	if not in_scope:
		reasons, status = [], "Clear"
	elif reasons:
		status = "Stopped"
	elif exposure >= (limit + cap) * flt(s.credit_warning_percent) / 100:
		status = "Warning"
	else:
		status = "Clear"

	return frappe._dict(
		in_scope=cint(in_scope),
		insured_limit=insured,
		committee_limit=committee,
		operating_limit=limit,
		billed_amount=billed,
		unbilled_amount=unbilled,
		exposure=exposure,
		amount_remaining=limit + cap - exposure,
		credit_status=status,
		stop_reasons=",".join(reasons),
		overdue_amount=od.amount,
		days=od.days,
		exception_cap=cap,
		exception_lifts=",".join(lifts),
		exception_valid_until=min((x.expires_at for x in live), default=None),
	)
