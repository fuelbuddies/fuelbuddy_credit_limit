# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""One customer's credit figures: limits, exposure, overdue, live exceptions and stop status. Never writes."""

import frappe
from erpnext import get_default_company
from erpnext.setup.utils import get_exchange_rate
from frappe.utils import cint, flt, getdate, now_datetime

from fuelbuddy_creditlimit.overdue import OpenInvoice, overdue
from fuelbuddy_creditlimit.unbilled import Delivery, unbilled_qty, unbilled_share


def credit_settings():
	"""The credit fields of Fuelbuddy Settings. Blank or 0 means the default: Singles get no defaults on migrate."""
	s = frappe.db.get_singles_dict("Fuelbuddy Settings")
	return frappe._dict(
		credit_insured_factor=flt(s.custom_credit_insured_factor) or 80,
		credit_warning_percent=flt(s.custom_credit_warning_percent) or 80,
		credit_overdue_grace_days=cint(s.custom_credit_overdue_grace_days) or 15,
		credit_exception_max_days=cint(s.custom_credit_exception_max_days) or 7,
	)


def _has_wallet(customer):
	return bool(frappe.db.exists("Wallet", {"customer": customer, "payment_type": "Wallet"}))


def is_cash(customer):
	"""Cash customers pay in advance: a Wallet, or customer payment terms with no credit days (Cash advance).
	Invoice terms play no part: they come from the deal and can lag a customer's move to cash."""
	if _has_wallet(customer):
		return True
	terms = frappe.db.get_value("Customer", customer, "payment_terms")
	if not terms:
		return False
	days = frappe.db.sql(
		"select max(credit_days) from `tabPayment Terms Template Detail` where parent = %s", terms
	)[0][0]
	return days is not None and not cint(days)


def is_in_scope(customer):
	"""Credit customers: enabled and not cash."""
	if frappe.db.get_value("Customer", customer, "disabled"):
		return False
	return not is_cash(customer)


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


# Every delivery of the customer up to %(d)s, one copy per platform delivery: of two Delivery Notes for one
# delivery, the submitted one, then the newest. gq is the stock quantity delivered on the line, sq what is left
# of it after the submitted returns against that line up to %(d)s.
_DELIVERIES = """
	select dn.name, dn.posting_date, dn.creation, dn.base_grand_total, dn.net_total, dni.idx, dni.item_code,
		dni.so_detail, dni.against_sales_order, dni.qty, dni.amount, if(dni.stock_qty, dni.stock_qty, dni.qty) gq,
		if(dni.stock_qty, dni.stock_qty, dni.qty) - coalesce(ret.rq, 0) sq,
		dense_rank() over (partition by coalesce(nullif(dn.custom_invoiced_item_id, ''), dn.name)
			order by dn.docstatus desc, dn.creation desc, dn.name desc) copy
	from `tabDelivery Note` dn join `tabDelivery Note Item` dni on dni.parent = dn.name
	left join (
		select ri.dn_detail, -sum(if(ri.stock_qty, ri.stock_qty, ri.qty)) rq
		from `tabDelivery Note` rn join `tabDelivery Note Item` ri on ri.parent = rn.name
		where rn.customer = %(c)s and rn.docstatus = 1 and rn.is_return = 1 and rn.posting_date <= %(d)s
		group by ri.dn_detail
	) ret on ret.dn_detail = dni.name
	where dn.customer = %(c)s and dn.docstatus < 2 and dn.is_return = 0 and dn.posting_date <= %(d)s"""


def unbilled_by_dn(customer, as_of):
	"""{Delivery Note: unbilled value incl. VAT, company currency}. Per item, litres delivered net of returns minus
	litres invoiced over all time, taken from the newest deliveries (unbilled.py), valued as the invoice will value
	them."""
	# imported here so a wallet build without the helper fails only this job, never a DN/SI/PE/JE hook
	from fuelbuddy_wallet.fuelbuddy_wallet.doctype.wallet.wallet import _billable_rows

	d = getdate(as_of)
	args = {"c": customer, "d": d}
	delivered = dict(
		frappe.db.sql(
			f"select item_code, sum(sq) from ({_DELIVERIES}) x where copy = 1 group by item_code",
			args,
		)
	)
	# opening invoices bill balances from before go-live and carry no item, so they never match a delivery
	invoiced = dict(
		frappe.db.sql(
			"""select sii.item_code, sum(if(sii.stock_qty, sii.stock_qty, sii.qty))
			from `tabSales Invoice Item` sii join `tabSales Invoice` si on si.name = sii.parent
			where si.customer = %(c)s and si.docstatus = 1 and ifnull(si.is_opening, 'No') != 'Yes'
				and si.posting_date <= %(d)s
			group by sii.item_code""",
			args,
		)
	)
	need = unbilled_qty({i: flt(q) for i, q in delivered.items()}, {i: flt(q) for i, q in invoiced.items()})
	rows = []
	for item, qty in need.items():
		# only the newest deliveries that cover the unbilled quantity; unbilled_share() decides, this just trims
		rows += frappe.db.sql(
			f"""select * from (
				select x.*, sum(sq) over (order by posting_date desc, creation desc, name desc, idx desc) run
				from ({_DELIVERIES}) x where copy = 1 and item_code = %(item)s and sq > 0
			) y where run - sq < %(qty)s""",
			{**args, "item": item, "qty": qty},
			as_dict=True,
		)
	share = unbilled_share(
		[
			Delivery(i, r.item_code, (r.posting_date, r.creation, r.name, r.idx), flt(r.sq))
			for i, r in enumerate(rows)
		],
		need,
	)
	# share is of what is left after returns; qty and amount are the whole line, so scale to sq / gq of it
	picked = [(r, share[i] * flt(r.sq) / flt(r.gq)) for i, r in enumerate(rows) if i in share]
	values = _billable_rows(
		customer,
		[
			(
				r.so_detail,
				r.against_sales_order,
				r.posting_date,
				r.item_code,
				flt(r.qty) * f,
				flt(r.amount) * f,
			)
			for r, f in picked
		],
		d,
	)
	out = {}
	for (r, _f), (value, _fm) in zip(picked, values, strict=True):
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
	unbilled = sum(unbilled_by_dn(customer, as_of).values())
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
	if od.amount > 0 and "overdue" not in lifts:
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
