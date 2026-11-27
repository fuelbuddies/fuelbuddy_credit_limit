# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""Unbilled deliveries. Invoice lines name a Sales Order line, never a Delivery Note, and are often booked on a
line other than the one carrying the deliveries, so no delivery can be matched to its invoice. Instead, per item,
a customer's litres delivered minus litres invoiced, over all time, are unbilled, and they are the newest
deliveries. No date window: an old delivery never invoiced stays unbilled until it is. Pure: no database."""

from typing import NamedTuple


class Delivery(NamedTuple):
	key: object  # the caller's handle for the row
	item: str
	order: tuple  # sort key, oldest first
	qty: float  # stock quantity


def unbilled_qty(delivered, invoiced):
	"""{item: delivered minus invoiced stock quantity} for items with some left unbilled. Both maps are per item;
	delivered is net of returns, invoiced net of credit notes. Invoicing more than was delivered leaves nothing
	unbilled, never less."""
	out = {}
	for item, qty in delivered.items():
		left = qty - invoiced.get(item, 0.0)
		if left > 1e-6:
			out[item] = left
	return out


def unbilled_share(deliveries, unbilled):
	"""{key: unbilled share, 0 to 1}. unbilled = unbilled_qty(); per item it covers the newest deliveries, the
	oldest of those possibly in part. Fully invoiced deliveries are left out."""
	left = dict(unbilled)
	out = {}
	for dv in sorted(deliveries, key=lambda x: x.order, reverse=True):
		need = left.get(dv.item, 0.0)
		if dv.qty <= 0 or need <= 1e-6:
			continue
		take = min(dv.qty, need)
		left[dv.item] = need - take
		out[dv.key] = take / dv.qty
	return out
