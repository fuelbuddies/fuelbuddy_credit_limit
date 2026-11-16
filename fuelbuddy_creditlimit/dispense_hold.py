# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""Dispense holds (IDEV-3496): what one fill at the pump may take from the customer's wallet, credit and
Sales Orders, held from the platform's limit call until the fill's Delivery Note is saved in ERP.

One hold per fill (platform task + customer asset). It counts against every pool that applies:
  - wallet: cash customers (credit_engine.is_cash); no Wallet means nothing can be dispensed;
  - credit: credit customers; no Credit Limit record, or a Stopped one, means nothing can be dispensed;
  - sales_order: every customer, on the Sales Orders erp-functions would build the Delivery Note from.
Available in a pool = its live ERP balance - the open holds in it. The Delivery Note takes its fill's hold
over in the same transaction (take_over_hold), so a fill is counted once: by its hold, then by its DN.

Called by the platform through erp-functions. Grant: ERP decides the litres (the platform's flag gives ERP
the controlling vote). Record: the platform decided and ERP records the hold, past its balances if need be,
so both systems hold the same fills whichever has the vote. Shaped like fuelbuddy_crm's qc_hold API: one
locked transaction per call, a {ok, code, message, retryable, result} answer, gated on writing Delivery Notes.
"""

import math

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime
from frappe.utils.data import strip_html

from fuelbuddy_creditlimit.credit_engine import compute_credit, is_cash
from fuelbuddy_creditlimit.dispense_rules import grant, litres_for, so_line_litres

DOCTYPE = "Dispense Hold"
OPEN, CONSUMED, RELEASED, EXPIRED = "Open", "Consumed", "Released", "Expired"
GRANT, RECORD = "Grant", "Record"
REASONS = ("Cancelled", "Not Dispensed", "Expired")
WALLET, CREDIT, SALES_ORDER = "wallet", "credit", "sales_order"
RETRYABLE = {"LOCK_RETRY"}
MAX_LENGTH = 140
ISSUE_ASSIGNEE_FIELD = "custom_dispense_hold_issue_assignee"

# result codes of a successful call
HELD, ALREADY_HELD, NOTHING_GRANTED, ALREADY_DELIVERED = (
	"HELD",
	"ALREADY_HELD",
	"NOTHING_GRANTED",
	"ALREADY_DELIVERED",
)
RELEASED_NOW, NOTHING_TO_RELEASE, AVAILABLE, FOUND, NOT_FOUND = (
	"RELEASED",
	"NOTHING_TO_RELEASE",
	"AVAILABLE",
	"FOUND",
	"NOT_FOUND",
)


class Refusal(Exception):
	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


# ---- the API -------------------------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"])
def reserve(
	customer,
	shipping_address,
	posting_date,
	litres,
	task_id,
	customer_asset_id,
	mode=GRANT,
	fallback_rate=None,
	platform_ref=None,
):
	"""Hold a fill. Grant: up to ``litres``, as far as the pools allow. Record: exactly ``litres``.
	Idempotent per fill: a fill with an open hold gets that hold back."""
	try:
		args = frappe._dict(
			customer=_required(customer, "customer"),
			shipping_address=_required(shipping_address, "shipping_address"),
			posting_date=_date(posting_date, "posting_date"),
			litres=_positive(litres, "litres"),
			task_id=_required(task_id, "task_id"),
			customer_asset_id=_required(customer_asset_id, "customer_asset_id"),
			mode=_one_of(mode or GRANT, (GRANT, RECORD), "mode"),
			fallback_rate=flt(fallback_rate),
			platform_ref=(str(platform_ref).strip()[:MAX_LENGTH] if platform_ref else None),
		)
		_check_permitted()
	except Refusal as refusal:
		return _refused(refusal)
	return _in_transaction(lambda: _reserve(args))


@frappe.whitelist(methods=["POST"])
def release(task_id, customer_asset_id=None, reason="Cancelled"):
	"""Release a fill's open holds (all of the task's, without an asset). Expired raises an Issue per hold."""
	try:
		args = frappe._dict(
			task_id=_required(task_id, "task_id"),
			customer_asset_id=(
				_required(customer_asset_id, "customer_asset_id") if customer_asset_id else None
			),
			reason=_one_of(reason or "Cancelled", REASONS, "reason"),
		)
		_check_permitted()
	except Refusal as refusal:
		return _refused(refusal)
	return _in_transaction(lambda: _release(args))


@frappe.whitelist()
def availability(customer, shipping_address, posting_date, litres=None, fallback_rate=None):
	"""What a fill could get now, pool by pool. Read only: no lock, no hold."""
	try:
		customer = _required(customer, "customer")
		shipping_address = _required(shipping_address, "shipping_address")
		d = _date(posting_date, "posting_date")
		wanted = flt(litres) if litres not in (None, "") else None
		_check_permitted()
		view = _availability(customer, shipping_address, d, wanted, flt(fallback_rate))
	except Refusal as refusal:
		return _refused(refusal)
	return _ok(AVAILABLE, _("Availability for {0}").format(customer), view)


@frappe.whitelist()
def hold_status(task_id, customer_asset_id):
	"""The fill's latest hold, for the platform's workflow to follow."""
	try:
		task_id = _required(task_id, "task_id")
		customer_asset_id = _required(customer_asset_id, "customer_asset_id")
		_check_permitted()
	except Refusal as refusal:
		return _refused(refusal)
	hold = _latest_hold(task_id, customer_asset_id)
	if not hold:
		return _ok(NOT_FOUND, _("No hold for task {0}, asset {1}").format(task_id, customer_asset_id), None)
	return _ok(FOUND, _("Hold {0} is {1}").format(hold.name, hold.status), _hold_result(hold))


# ---- reserve -------------------------------------------------------------------------------------------
def _reserve(a):
	# a snapshot taken before the lock could miss a hold committed while this call waited for it
	frappe.db.commit()
	# Lock order: customer, the fill's holds, then the wallet. A Delivery Note save locks its own hold row,
	# then the wallet (its ledger entry), so taking the wallet last here leaves no cycle to deadlock on.
	_lock_customer(a.customer)
	held = _open_hold(a.task_id, a.customer_asset_id)
	if held:
		return _ok(ALREADY_HELD, _("Fill already held by {0}").format(held.name), _hold_result(held))
	_lock_wallet(a.customer)

	view = _availability(a.customer, a.shipping_address, a.posting_date, a.litres, a.fallback_rate)
	delivered = _delivery_note_for(a.task_id, a.customer_asset_id)
	if delivered:
		# Record mode, ERP was late: the DN is already in, so the fill is counted by it. Keep the history.
		hold = _insert(a, view, a.litres, CONSUMED, consumed_by=delivered)
		return _ok(
			ALREADY_DELIVERED, _("{0} already delivered this fill").format(delivered), _hold_result(hold)
		)

	litres = view.litres if a.mode == GRANT else a.litres
	if litres <= 0:
		return _ok(
			NOTHING_GRANTED,
			_("Nothing can be dispensed to {0}: {1}").format(a.customer, view.limited_by),
			{**view, "hold": None},
		)
	hold = _insert(a, view, litres, OPEN)
	return _ok(HELD, _("Held {0} L for task {1}").format(litres, a.task_id), _hold_result(hold, view))


def _insert(a, view, litres, status, consumed_by=None):
	rate = flt(view.rate)
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"customer": a.customer,
			"task_id": a.task_id,
			"customer_asset_id": a.customer_asset_id,
			"posting_date": a.posting_date,
			"status": status,
			"mode": a.mode,
			"platform_ref": a.platform_ref,
			"litres": litres,
			"rate": rate,
			"amount": flt(litres * rate, 2),
			"rate_source": view.rate_source,
			"would_grant_litres": view.litres,
			"limited_by": view.limited_by,
			"in_wallet": cint(view.pools[WALLET].counts),
			"in_credit": cint(view.pools[CREDIT].counts),
			"in_sales_order": cint(view.pools[SALES_ORDER].counts),
			"shipping_address": view.shipping_address,
			"billing_address": view.billing_address,
			"consumed_by": consumed_by,
			"closed_at": now_datetime() if status != OPEN else None,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


# ---- availability --------------------------------------------------------------------------------------
def _availability(customer, shipping_address, d, wanted, fallback_rate):
	"""Each pool's litres left for a new fill of this customer on this shipping address, and the grant."""
	disabled = frappe.db.get_value("Customer", customer, "disabled")
	if disabled is None:
		raise Refusal("ERP_VALIDATION", _("Customer {0} not found").format(customer))
	shipping = shipping_address if frappe.db.exists("Address", shipping_address) else None
	billing = (shipping and frappe.db.get_value("Address", shipping, "custom_billing_address")) or None
	cash = is_cash(customer)

	lines = _sales_order_lines(customer, billing, d) if billing else []
	rate, rate_source = _rate(customer, lines, d, wanted, fallback_rate)

	pools = frappe._dict()
	pools[SALES_ORDER] = _sales_order_pool(customer, billing, shipping, lines)
	pools[WALLET] = _wallet_pool(customer, rate) if cash else _not_applicable()
	pools[CREDIT] = _not_applicable() if cash else _credit_pool(customer, rate)
	if disabled:
		for pool in pools.values():
			pool.update(litres=0.0, reason="customer_disabled")

	litres, limited_by = grant(
		wanted if wanted is not None else math.inf,
		{name: (pool.litres if pool.applies else None) for name, pool in pools.items()},
	)
	if limited_by in pools and pools[limited_by].reason:
		limited_by = pools[limited_by].reason
	return frappe._dict(
		customer=customer,
		payment_mode="Cash" if cash else "Credit",
		shipping_address=shipping,
		billing_address=billing,
		rate=rate,
		rate_source=rate_source,
		litres=None if math.isinf(litres) else litres,  # none: nothing wanted and no pool applies
		limited_by=limited_by,
		pools=pools,
	)


def _not_applicable():
	return frappe._dict(applies=False, counts=False, litres=None, amount=None, reason=None)


def _wallet_pool(customer, rate):
	from fuelbuddy_wallet.fuelbuddy_wallet.doctype.wallet.wallet import wallet_balance

	w = wallet_balance(customer)
	if not w:
		return frappe._dict(applies=True, counts=False, litres=0.0, amount=0.0, reason="no_wallet")
	amount = w.remaining + w.headroom - open_wallet_hold_amount(customer)
	return frappe._dict(
		applies=True,
		counts=True,
		amount=amount,
		litres=max(0.0, litres_for(amount, rate)),
		reason=None if rate else "no_rate",
		wallet=w.name,
	)


def _credit_pool(customer, rate):
	if not frappe.db.exists("Credit Limit", {"customer": customer}):
		return frappe._dict(applies=True, counts=False, litres=0.0, amount=0.0, reason="no_credit_limit")
	c = compute_credit(customer)
	if c.credit_status == "Stopped":
		return frappe._dict(
			applies=True,
			counts=True,
			litres=0.0,
			amount=0.0,
			reason="credit_stopped:" + (c.stop_reasons or ""),
		)
	amount = flt(c.amount_remaining) - _open_amount(customer, "in_credit")
	return frappe._dict(
		applies=True,
		counts=True,
		amount=amount,
		litres=max(0.0, litres_for(amount, rate)),
		reason=None if rate else "no_rate",
		credit_status=c.credit_status,
	)


def _sales_order_pool(customer, billing, shipping, lines):
	if not shipping:
		return frappe._dict(applies=True, counts=False, litres=0.0, amount=None, reason="no_shipping_address")
	if not billing:
		return frappe._dict(applies=True, counts=False, litres=0.0, amount=None, reason="no_billing_address")
	if not lines:
		return frappe._dict(applies=True, counts=True, litres=0.0, amount=None, reason="no_sales_order")
	left = sum(max(0.0, so_line_litres(line)) for line in lines)
	held = flt(
		frappe.db.sql(
			f"""select coalesce(sum(litres), 0) from `tab{DOCTYPE}`
			where customer = %s and billing_address = %s and status = %s and in_sales_order = 1""",
			(customer, billing, OPEN),
		)[0][0]
	)
	return frappe._dict(
		applies=True,
		counts=True,
		litres=max(0.0, left - held),
		amount=None,
		reason="no_sales_order" if left <= 0 else None,
		sales_orders=[line.parent for line in lines],
	)


def _sales_order_lines(customer, billing, d):
	"""The line erp-functions allocates a Delivery Note to on each Sales Order it would use, oldest first:
	the customer's submitted orders on the billing address whose dates cover the day (main's buildSalesOrderQuery),
	each by its first line (fetchCandidates)."""
	return frappe.db.sql(
		"""select soi.name, soi.parent, soi.item_code, soi.qty, soi.delivered_qty, soi.returned_qty,
			soi.custom_delivery_note_qty_in_draft, soi.conversion_factor, so.base_grand_total, so.net_total
		from `tabSales Order` so
		join `tabSales Order Item` soi on soi.parent = so.name and soi.parenttype = 'Sales Order'
			and soi.idx = (select min(i.idx) from `tabSales Order Item` i where i.parent = so.name)
		where so.customer = %(c)s and so.customer_address = %(b)s and so.docstatus = 1
			and so.transaction_date <= %(d)s and so.delivery_date >= %(d)s
		order by so.creation asc""",
		{"c": customer, "b": billing, "d": d},
		as_dict=True,
	)


def _rate(customer, lines, d, wanted, fallback_rate):
	"""(AED per litre incl. VAT, source) the invoice will bill this fill at: the wallet's _billable_rows on the
	first line with litres left (Force Majeure first, then the line's price and the deal discount); with no
	line to price, the platform's rate."""
	from fuelbuddy_wallet.fuelbuddy_wallet.doctype.wallet.wallet import _billable_rows

	line = next((x for x in lines if so_line_litres(x) > 0), lines[0] if lines else None)
	if line:
		qty = flt(wanted) if wanted and math.isfinite(flt(wanted)) and flt(wanted) > 0 else 1.0
		value, fm = _billable_rows(customer, [(line.name, line.parent, d, line.item_code, qty, 0.0)], d)[0]
		net = flt(line.net_total)
		vat = flt(line.base_grand_total) / net if net else 1.0
		rate = flt(value) / qty * vat
		if rate > 0:
			return rate, ("Force Majeure" if fm else "Sales Order")
	if fallback_rate > 0:
		return fallback_rate, "Platform"
	return 0.0, None


def open_wallet_hold_amount(customer):
	"""Hook ``wallet_open_holds``: value held in the wallet for fills whose Delivery Notes are not in yet."""
	return _open_amount(customer, "in_wallet")


def _open_amount(customer, pool_field):
	return flt(
		frappe.db.sql(
			f"select coalesce(sum(amount), 0) from `tab{DOCTYPE}` where customer = %s and status = %s and {pool_field} = 1",
			(customer, OPEN),
		)[0][0]
	)


# ---- release -------------------------------------------------------------------------------------------
def _release(a):
	holds = _locked_open_holds(a.task_id, a.customer_asset_id)
	if not holds:
		return _ok(NOTHING_TO_RELEASE, _("No open hold for task {0}").format(a.task_id), {"released": []})
	status = EXPIRED if a.reason == "Expired" else RELEASED
	now = now_datetime()
	for hold in holds:
		frappe.db.set_value(
			DOCTYPE, hold.name, {"status": status, "release_reason": a.reason, "closed_at": now}
		)
		if status == EXPIRED:
			_raise_issue(
				_("Dispense hold expired: {0}").format(hold.customer),
				hold,
				_(
					"The fill's Delivery Note did not reach ERP before the hold expired, so the hold was released."
				),
			)
	return _ok(
		RELEASED_NOW,
		_("{0} hold(s) for task {1} {2}").format(len(holds), a.task_id, status.lower()),
		{
			"released": [h.name for h in holds],
			"status": status,
			"litres": sum(flt(h.litres) for h in holds),
			"amount": sum(flt(h.amount) for h in holds),
		},
	)


# ---- the Delivery Note takes its hold over ---------------------------------------------------------------
def take_over_hold(doc, method=None):
	"""Delivery Note ``before_save`` / ``before_submit``, on the first punch of a pump fill: the DN takes over
	its fill's hold in the same transaction, so the fill stops counting as held exactly when it counts as
	delivered. Never refuses the DN: the fuel is already in the vehicle. Anything unexpected raises an Issue."""
	if not doc.flags.in_insert or doc.amended_from or doc.is_return:
		return
	task_id, asset_id = doc.get("custom_task_id"), doc.get("custom_customer_asset_id")
	if not (task_id and asset_id):
		return  # a desk Delivery Note: no fill at the pump behind it
	found = _latest_hold(task_id, asset_id)
	if not found:
		return  # not recorded yet: reserve() finds this DN and records the hold as consumed
	# lock just this row, by name: no range locks for a reserve's insert to wait behind
	hold = frappe.db.sql(
		f"select {_HOLD_FIELDS} from `tab{DOCTYPE}` where name = %s for update", found.name, as_dict=True
	)[0]
	doc.custom_dispense_hold = hold.name
	if hold.status == OPEN:
		frappe.db.set_value(
			DOCTYPE, hold.name, {"status": CONSUMED, "consumed_by": doc.name, "closed_at": now_datetime()}
		)
		value = _delivery_note_value(doc)
		if value > flt(hold.amount) + 0.01:
			_raise_issue(
				_("Delivered more than held: {0}").format(doc.customer),
				hold,
				_("Delivery Note {0} is worth {1}; its hold was {2}.").format(
					doc.name, value, flt(hold.amount)
				),
			)
	elif hold.status in (RELEASED, EXPIRED):
		_raise_issue(
			_("Delivery Note for a {0} hold: {1}").format(hold.status.lower(), doc.customer),
			hold,
			_("Delivery Note {0} arrived after its hold was {1}.").format(doc.name, hold.status.lower()),
		)
	elif hold.consumed_by != doc.name and not _is_live_delivery_note(hold.consumed_by):
		frappe.db.set_value(DOCTYPE, hold.name, "consumed_by", doc.name)  # re-punched after its DN went


def _delivery_note_value(doc):
	from fuelbuddy_wallet.fuelbuddy_wallet.doctype.wallet.wallet import _doc_dn_value

	return flt(_doc_dn_value(doc))


def _is_live_delivery_note(name):
	return bool(name) and frappe.db.get_value("Delivery Note", name, "docstatus") in (0, 1)


def _delivery_note_for(task_id, asset_id):
	meta = frappe.get_meta("Delivery Note")
	if not (meta.has_field("custom_task_id") and meta.has_field("custom_customer_asset_id")):
		return None
	return frappe.db.get_value(
		"Delivery Note",
		{
			"custom_task_id": task_id,
			"custom_customer_asset_id": asset_id,
			"docstatus": ("<", 2),
			"is_return": 0,
		},
		"name",
	)


# ---- lookups -------------------------------------------------------------------------------------------
def _lock_customer(customer):
	"""Serialize the customer's reserves on the customer's row."""
	if not frappe.db.sql("select name from `tabCustomer` where name = %s for update", customer):
		raise Refusal("ERP_VALIDATION", _("Customer {0} not found").format(customer))


def _lock_wallet(customer):
	"""Serialize with a desk Delivery Note's wallet check, which locks the same row before it reads the holds."""
	frappe.db.sql(
		"select name from `tabWallet` where customer = %s and payment_type = 'Wallet' for update", customer
	)


_HOLD_FIELDS = "name, customer, status, litres, amount, consumed_by, mode, would_grant_litres, limited_by"


def _open_hold(task_id, asset_id):
	rows = frappe.db.sql(
		f"""select {_HOLD_FIELDS} from `tab{DOCTYPE}`
		where task_id = %s and customer_asset_id = %s and status = %s for update""",
		(task_id, asset_id, OPEN),
		as_dict=True,
	)
	return rows[0] if rows else None


def _locked_open_holds(task_id, asset_id):
	cond, params = "task_id = %s and status = %s", [task_id, OPEN]
	if asset_id:
		cond += " and customer_asset_id = %s"
		params.append(asset_id)
	return frappe.db.sql(
		f"select {_HOLD_FIELDS}, task_id, customer_asset_id from `tab{DOCTYPE}` where {cond} for update",
		params,
		as_dict=True,
	)


def _latest_hold(task_id, asset_id):
	"""The fill's open hold, else its newest. A plain read."""
	rows = frappe.db.sql(
		f"""select {_HOLD_FIELDS} from `tab{DOCTYPE}` where task_id = %s and customer_asset_id = %s
		order by (status = %s) desc, creation desc limit 1""",
		(task_id, asset_id, OPEN),
		as_dict=True,
	)
	return rows[0] if rows else None


def _hold_result(hold, view=None):
	out = {
		"hold": hold.name,
		"status": hold.status,
		"mode": hold.mode,
		"litres": flt(hold.litres),
		"amount": flt(hold.amount),
		"would_grant_litres": flt(hold.would_grant_litres),
		"limited_by": hold.limited_by,
		"consumed_by": hold.consumed_by,
	}
	if view:
		out["pools"] = view.pools
		out["rate"] = view.rate
	return out


# ---- Issues --------------------------------------------------------------------------------------------
def _raise_issue(subject, hold, detail):
	"""An Issue for something about a hold that needs a person, assigned to the settings' user. Never fails
	its caller: a hold is released, and a Delivery Note saved, whether or not the Issue could be raised."""
	try:
		frappe.db.savepoint("dispense_hold_issue")
		return _insert_issue(subject, hold, detail)
	except Exception:
		frappe.db.rollback(save_point="dispense_hold_issue")
		frappe.log_error(title=f"Dispense hold Issue not raised: {hold.name}", message=frappe.get_traceback())


def _insert_issue(subject, hold, detail):
	issue = frappe.get_doc(
		{
			"doctype": "Issue",
			"subject": subject[:MAX_LENGTH],
			"priority": "High",
			"issue_type": "Error Log",
			"customer": hold.customer,
			"description": "<br>".join(
				[
					detail,
					f"Dispense Hold: {hold.name}",
					f"Status: {hold.status}",
					f"Litres: {flt(hold.litres)}",
					f"Amount: {flt(hold.amount)}",
				]
			),
		}
	).insert(ignore_permissions=True)
	assignee = frappe.db.get_single_value("Fuelbuddy Settings", ISSUE_ASSIGNEE_FIELD)
	if assignee:
		from frappe.desk.form.assign_to import add as assign

		assign({"assign_to": [assignee], "doctype": "Issue", "name": issue.name, "description": subject})
	return issue.name


# ---- helpers (as fuelbuddy_crm.api.qc_hold) --------------------------------------------------------------
def _in_transaction(step):
	"""Run one locked read-and-write and commit it, or roll back and answer with the refusal."""
	try:
		result = step()
		frappe.db.commit()
		return result
	except Exception as exc:
		frappe.db.rollback()
		refusal = _as_refusal(exc)
		if refusal is None:
			raise  # infrastructure: an HTTP error, which erp-functions treats as retryable
		return _refused(refusal)


def _check_permitted():
	# The same gate as fuelbuddy_crm's qc_hold: a hold stands for the Delivery Note the fill will become.
	if not frappe.has_permission("Delivery Note", "write"):
		raise Refusal("ERP_VALIDATION", _("Not permitted to hold fills for Delivery Notes"))


def _required(value, field):
	text = (str(value) if value is not None else "").strip()
	if not text:
		raise Refusal("ERP_VALIDATION", _("{0} is required").format(field))
	if len(text) > MAX_LENGTH:
		raise Refusal("ERP_VALIDATION", _("{0} must be at most {1} characters").format(field, MAX_LENGTH))
	return text


def _positive(value, field):
	try:
		number = float(value)
	except (TypeError, ValueError):
		raise Refusal("ERP_VALIDATION", _("{0} must be a number, got {1!r}").format(field, value))
	if not math.isfinite(number) or number <= 0:
		raise Refusal("ERP_VALIDATION", _("{0} must be more than 0").format(field))
	return number


def _date(value, field):
	try:
		return getdate(_required(value, field))
	except Refusal:
		raise
	except Exception:
		raise Refusal("ERP_VALIDATION", _("{0} must be a date, got {1!r}").format(field, value))


def _one_of(value, allowed, field):
	if value not in allowed:
		raise Refusal("ERP_VALIDATION", _("{0} must be one of {1}").format(field, ", ".join(allowed)))
	return value


def _ok(result_code, message, result):
	return {"ok": True, "code": result_code, "message": message, "retryable": False, "result": result}


def _refused(refusal):
	frappe.clear_messages()
	return {
		"ok": False,
		"code": refusal.code,
		"message": refusal.message,
		"retryable": refusal.code in RETRYABLE,
		"result": None,
	}


def _as_refusal(exc):
	"""Map an exception raised inside the transaction to its code; None = not ours to answer."""
	if isinstance(exc, Refusal):
		return exc
	if isinstance(
		exc,
		frappe.QueryDeadlockError
		| frappe.QueryTimeoutError
		| frappe.TimestampMismatchError
		| frappe.DuplicateEntryError
		| frappe.UniqueValidationError,
	):
		return Refusal("LOCK_RETRY", _message(exc))
	if _is_lock_error(exc):
		return Refusal("LOCK_RETRY", _message(exc))
	if isinstance(exc, frappe.ValidationError | frappe.PermissionError):
		return Refusal("ERP_VALIDATION", _message(exc))
	return None


def _is_lock_error(exc):
	try:
		return bool(frappe.db.is_deadlocked(exc) or frappe.db.is_timedout(exc))
	except Exception:
		return False


def _message(exc):
	return strip_html(str(exc) or exc.__class__.__name__)[:500]
