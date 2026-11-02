// Copyright (c) 2026, Fuelbuddy and contributors
// For license information, please see license.txt

frappe.ui.form.on("Credit Limit", {
	refresh(frm) {
		if (!frm.is_new() && frappe.user.has_role(["Credit Controller", "System Manager"])) {
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
	const rows = Object.keys(res.expected)
		.map((f) => {
			const money = frappe.meta.get_docfield(frm.doctype, f).fieldtype === "Currency";
			const fmt = (v) => (money ? format_currency(v, currency) : cstr(v));
			const bad = money
				? Math.abs(res.stored[f] - res.expected[f]) >= 0.005
				: res.stored[f] !== res.expected[f];
			return `<tr${bad ? ' style="color:var(--red-600);font-weight:600;"' : ""}>
				<td>${__(frappe.meta.get_label(frm.doctype, f))}</td>
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
				callback: (r) => {
					d.hide();
					frm.reload_doc();
					// no record name back = credit control is switched off and nothing was stored
					frappe.show_alert(
						r.message
							? { message: __("Credit Limit corrected"), indicator: "green" }
							: { message: __("Credit control is switched off; nothing was corrected"), indicator: "orange" }
					);
				},
			});
		},
	});
	d.$body.html(table);
	d.show();
}
