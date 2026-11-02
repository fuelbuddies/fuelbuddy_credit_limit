# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""Approval workflow for Credit Exception; created once by patch so later desk tweaks are not overwritten.
Frappe's Workflow.on_update adds the ``workflow_state`` Custom Field itself."""

import frappe

WORKFLOW = "Credit Exception Approval"
CC = "Credit Controller"
BH = "Business Head"
CH = "Country Head"
STATES = (
	"Draft",
	"Pending Finance",
	"Pending Business Head",
	"Pending Country Head",
	"Approved",
	"Rejected",
	"Cancelled",
)
ACTIONS = ("Submit for Approval", "Approve", "Reject", "Cancel")


def _ensure(doctype, name, **values):
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)


def _t(state, action, next_state, allowed, condition=None, self_approval=0):
	return {
		"state": state,
		"action": action,
		"next_state": next_state,
		"allowed": allowed,
		"condition": condition,
		"allow_self_approval": self_approval,
	}


def execute():
	for state in STATES:
		_ensure("Workflow State", state, workflow_state_name=state, style="")
	for action in ACTIONS:
		_ensure("Workflow Action Master", action, workflow_action_name=action)

	if frappe.db.exists("Workflow", WORKFLOW):
		return

	frappe.get_doc(
		{
			"doctype": "Workflow",
			"workflow_name": WORKFLOW,
			"document_type": "Credit Exception",
			"workflow_state_field": "workflow_state",
			"is_active": 1,
			"send_email_alert": 0,
			"override_status": 0,
			"states": [
				{"state": "Draft", "doc_status": "0", "allow_edit": CC},
				{"state": "Pending Finance", "doc_status": "0", "allow_edit": CC},
				{"state": "Pending Business Head", "doc_status": "0", "allow_edit": BH},
				{"state": "Pending Country Head", "doc_status": "0", "allow_edit": CH},
				{"state": "Approved", "doc_status": "1", "allow_edit": CC},
				{"state": "Rejected", "doc_status": "0", "allow_edit": CC},
				{"state": "Cancelled", "doc_status": "2", "allow_edit": CC},
			],
			"transitions": [
				_t("Draft", "Submit for Approval", "Pending Finance", CC, self_approval=1),
				_t(
					"Pending Finance",
					"Approve",
					"Approved",
					CC,
					"not doc.needs_joint_approval and not doc.escalated",
				),
				_t(
					"Pending Finance",
					"Approve",
					"Pending Business Head",
					CC,
					"doc.needs_joint_approval and not doc.escalated",
				),
				_t("Pending Finance", "Approve", "Pending Country Head", CC, "doc.escalated"),
				_t("Pending Business Head", "Approve", "Approved", BH),
				_t("Pending Country Head", "Approve", "Approved", CH),
				# Business Head reject on a joint request is the tie: the Country Head decides
				_t("Pending Business Head", "Reject", "Pending Country Head", BH, self_approval=1),
				_t("Pending Finance", "Reject", "Rejected", CC, self_approval=1),
				_t("Pending Finance", "Reject", "Rejected", CH, self_approval=1),
				_t("Pending Business Head", "Reject", "Rejected", CH, self_approval=1),
				_t("Pending Country Head", "Reject", "Rejected", CH, self_approval=1),
				_t("Approved", "Cancel", "Cancelled", CC, self_approval=1),
				_t("Approved", "Cancel", "Cancelled", CH, self_approval=1),
			],
		}
	).insert(ignore_permissions=True)
