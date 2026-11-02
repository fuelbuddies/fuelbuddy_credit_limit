import frappe
from frappe import _
from frappe.utils import flt

from fuelbuddy_creditlimit.credit_engine import credit_settings

FIELDS = (
	"customer",
	"customer_name",
	"credit_status",
	"stop_reasons",
	"operating_limit",
	"insured_limit",
	"committee_limit",
	"billed_amount",
	"unbilled_amount",
	"exposure",
	"amount_remaining",
	"overdue_amount",
	"days",
	"exception_cap",
	"exception_valid_until",
	"last_refreshed",
)
MONEY = (
	"operating_limit",
	"insured_limit",
	"committee_limit",
	"billed_amount",
	"unbilled_amount",
	"amount_remaining",
	"overdue_amount",
	"exception_cap",
)


def execute(filters=None):
	warn = credit_settings().credit_warning_percent
	bands = [_("Uninsured"), _("Within limit"), _("Above {0}%").format(f"{warn:g}"), _("Above limit")]
	rows = frappe.get_list("Credit Limit", filters={"in_scope": 1}, fields=FIELDS, limit_page_length=0)

	for r in rows:
		e, i, room = flt(r.exposure), flt(r.insured_limit), flt(r.operating_limit) + flt(r.exception_cap)
		r.insured_part = min(max(e, 0), i)
		r.uninsured_exposure = max(e, 0) - r.insured_part
		r.used_percent = e / room * 100 if room > 0 else None
		r.band = bands[0 if not i else 3 if e >= room else 2 if e >= warn / 100 * room else 1]
	rows.sort(key=lambda r: (bands.index(r.band), -r.uninsured_exposure, -flt(r.exposure)))

	total = sum(max(flt(r.exposure), 0) for r in rows)
	insured = sum(r.insured_part for r in rows)
	room = sum(flt(r.operating_limit) + flt(r.exception_cap) for r in rows)
	oldest = min((r.last_refreshed for r in rows if r.last_refreshed), default=None)
	# Auto Email Report sends rows only, so the totals travel as the last row
	total_row = {f: sum(flt(r[f]) for r in rows) for f in MONEY}
	total_row.update(
		customer_name=_("Total"),
		exposure=total,
		uninsured_exposure=total - insured,
		used_percent=total / room * 100 if room > 0 else None,
		last_refreshed=oldest,
	)

	summary = [
		{"value": total, "label": _("Total exposure"), "datatype": "Currency"},
		{"value": insured, "label": _("Insured"), "datatype": "Currency", "indicator": "Green"},
		{"value": total - insured, "label": _("Uninsured"), "datatype": "Currency", "indicator": "Red"},
		{
			"value": (total - insured) / total * 100 if total else 0,
			"label": _("Uninsured %"),
			"datatype": "Percent",
		},
		{"value": oldest, "label": _("Oldest refresh"), "datatype": "Datetime"},
	]
	return get_columns(), [*rows, total_row], None, None, summary


def get_columns():
	def col(fieldname, label, fieldtype="Currency", width=130, **kw):
		return {"fieldname": fieldname, "label": _(label), "fieldtype": fieldtype, "width": width, **kw}

	return [
		# Credit Limit is named after the customer; a Customer link would need Customer read for every report role
		col("customer", "Customer", "Link", 150, options="Credit Limit"),
		col("customer_name", "Customer Name", "Data", 200),
		col("band", "Band", "Data", 110),
		col("credit_status", "Credit Status", "Data", 100),
		col("stop_reasons", "Stop Reasons", "Data", 150),
		col("operating_limit", "Operating Limit"),
		col("insured_limit", "Insured Limit"),
		col("committee_limit", "Committee Limit"),
		col("billed_amount", "Billed"),
		col("unbilled_amount", "Unbilled"),
		col("exposure", "Exposure"),
		col("amount_remaining", "Headroom"),
		col("used_percent", "Used %", "Percent", 90),
		col("uninsured_exposure", "Uninsured Exposure"),
		col("overdue_amount", "Overdue"),
		col("days", "Days Overdue", "Int", 90),
		col("exception_cap", "Exception Cap"),
		col("exception_valid_until", "Exception Valid Until", "Datetime", 160),
		col("last_refreshed", "Last Refreshed", "Datetime", 160),
	]
