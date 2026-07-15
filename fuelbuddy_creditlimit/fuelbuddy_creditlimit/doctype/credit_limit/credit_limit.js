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
			frm.add_custom_button(__("Reconcile from Transactions"), () => {
				frappe.call({
					method: "fuelbuddy_creditlimit.credit_control.check_customer_credit",
					args: { customer: frm.doc.customer },
					freeze: true,
					callback: (r) => show_credit_reconcile(frm, r.message),
				});
			});
		}
	},
});

// Dry-run first: show stored vs live figures; only recompute when the user confirms.
function show_credit_reconcile(frm, res) {
	const currency = frappe.defaults.get_default("currency");
	const labels = {
		amount_delivered: [__("Exposure (Amount Delivered)"), (v) => format_currency(v, currency)],
		amount_remaining: [__("Amount Remaining"), (v) => format_currency(v, currency)],
		days: [__("Days Overdue"), (v) => cstr(v)],
	};
	const rows = Object.keys(labels)
		.map((f) => {
			const [label, fmt] = labels[f];
			const bad = f === "days"
				? res.stored[f] !== res.expected[f]
				: Math.abs(res.stored[f] - res.expected[f]) >= 0.005;
			return `<tr${bad ? ' style="color:var(--red-600);font-weight:600;"' : ""}>
				<td>${label}</td>
				<td style="text-align:right">${fmt(res.stored[f])}</td>
				<td style="text-align:right">${fmt(res.expected[f])}</td>
			</tr>`;
		})
		.join("");
	const table = `<table class="table table-bordered">
		<thead><tr><th></th><th style="text-align:right">${__("Stored")}</th>
		<th style="text-align:right">${__("Live Transactions")}</th></tr></thead>
		<tbody>${rows}</tbody></table>`;

	if (res.in_sync) {
		frappe.msgprint({ title: __("Credit Limit is in sync"), message: table, indicator: "green" });
		return;
	}
	const d = new frappe.ui.Dialog({
		title: __("Credit Limit out of sync"),
		primary_action_label: __("Correct Now"),
		primary_action: () => {
			frappe.call({
				method: "fuelbuddy_creditlimit.credit_control.refresh_customer_credit",
				args: { customer: frm.doc.customer },
				freeze: true,
				callback: () => {
					d.hide();
					frm.reload_doc();
					frappe.show_alert({ message: __("Credit Limit corrected"), indicator: "green" });
				},
			});
		},
	});
	d.$body.html(table);
	d.show();
}
