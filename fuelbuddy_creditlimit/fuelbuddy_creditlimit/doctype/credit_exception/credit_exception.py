# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import (
	get_transitions,
	get_workflow,
	has_approval_access,
	is_transition_condition_satisfied,
)
from frappe.utils import add_days, add_to_date, cint, flt, get_datetime

from fuelbuddy_creditlimit.credit_engine import credit_settings

JOINT_APPROVAL_DAYS = 15
HEAD_STATES = ("Pending Business Head", "Pending Country Head")
LOCKED_FIELDS = ("customer", "cap", "valid_from", "expires_at", "lift_overdue", "lift_no_limit")


class CreditException(Document):
	def validate(self):
		if flt(self.cap) <= 0:
			frappe.throw(_("Cap must be greater than 0"))
		valid_from, expires_at = get_datetime(self.valid_from), get_datetime(self.expires_at)
		if expires_at <= valid_from:
			frappe.throw(_("Expires At must be after Valid From"))
		max_days = credit_settings().credit_exception_max_days
		if expires_at > add_days(valid_from, max_days):
			frappe.throw(_("An exception can last at most {0} days").format(max_days))

		self.days_overdue = cint(frappe.db.get_value("Credit Limit", {"customer": self.customer}, "days"))
		self.needs_joint_approval = cint(self.days_overdue > JOINT_APPROVAL_DAYS)
		recent = frappe.db.exists(
			"Credit Exception",
			{
				"customer": self.customer,
				"docstatus": 1,
				"name": ("!=", self.name),
				"expires_at": (">", add_to_date(valid_from, hours=-24)),
			},
		)
		self.escalated = 1 if recent else 0
		self.check_workflow_move()

	def check_workflow_move(self):
		# Frappe only checks that a transition exists, not who takes it or its condition on fresh values
		before = self.get_doc_before_save()
		if not before:
			if self.docstatus:
				frappe.throw(_("Approve this exception through the workflow."), frappe.PermissionError)
			return
		if before.get("workflow_state") in HEAD_STATES and any(
			self.has_value_changed(f) for f in LOCKED_FIELDS
		):
			frappe.throw(_("Finance has approved this request; its terms can no longer change."))
		state = self.get("workflow_state")
		if state == before.get("workflow_state") and self.docstatus == before.docstatus:
			return
		user = frappe.session.user
		workflow = get_workflow(self.doctype)
		moves = [
			t
			for t in get_transitions(before, workflow)
			if t.next_state == state and has_approval_access(user, before, t)
		]
		if not moves or not any(
			s.state == state and cint(s.doc_status) == self.docstatus for s in workflow.states
		):
			frappe.throw(_("Approve this exception through the workflow."), frappe.PermissionError)
		if not any(is_transition_condition_satisfied(t, self) for t in moves):
			frappe.throw(
				_(
					"Overdue days or recent exceptions changed since this request was raised. Save it and approve again."
				)
			)

	def before_submit(self):
		self.approved_by = frappe.session.user

	def before_cancel(self):
		# a direct cancel skips validate, so Frappe never moves the workflow state
		self.workflow_state = "Cancelled"
