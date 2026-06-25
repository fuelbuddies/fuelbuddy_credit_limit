// Copyright (c) 2026, Fuelbuddy and contributors
// For license information, please see license.txt

frappe.ui.form.on("Credit Limit", {
	refresh(frm) {
		if (frm.doc.enable_breach) {
			const reason = frm.doc.days > 0
				? __("overdue by {0} day(s)", [frm.doc.days])
				: __("credit limit exhausted");
			frm.dashboard.set_headline_alert(
				__("Credit breach — Delivery Notes are blocked ({0}).", [reason]),
				"red"
			);
		}
		if (!frm.is_new()) {
			frm.add_custom_button(__("Refresh from Transactions"), () => {
				frappe.call({
					method: "fuelbuddy_creditlimit.credit_control.refresh_customer_credit",
					args: { customer: frm.doc.customer },
					freeze: true,
					callback: () => frm.reload_doc(),
				});
			});
		}
	},
});
