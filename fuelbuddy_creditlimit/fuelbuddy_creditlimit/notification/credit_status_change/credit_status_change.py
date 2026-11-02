import frappe
from erpnext import get_default_company
from frappe.utils import fmt_money


def get_context(context):
	currency = frappe.get_cached_value("Company", get_default_company(), "default_currency")
	# the site's currency_precision (9 on prod) is too fine for a mail
	precision = len(str(frappe.get_cached_value("Currency", currency, "fraction_units") or 100)) - 1
	return {"money": lambda value: f"{currency} {fmt_money(value, precision)}"}
