# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""Credit roles; pre_model_sync because DocPerms link to tabRole during the sync."""

import frappe

ROLES = ("Credit Controller", "Business Head", "Country Head", "Credit Integration")


def execute():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)
