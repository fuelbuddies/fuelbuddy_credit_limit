# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""The arithmetic of dispense holds (IDEV-3496): how many litres a fill gets from its pools, what an amount
buys at a rate, and what is left on a Sales Order line. Pure: no database."""

import math


def _num(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def grant(wanted, pools):
	"""(whole litres, limited_by) a fill gets: the lowest of what is wanted and every pool that applies.
	``pools`` maps a pool to its litres left, None when the pool does not apply. Ties go to the first pool.
	Nothing wanted (infinity) and no pool applying gives infinity back, unrounded."""
	litres, limited_by = float(wanted), "wanted"
	for pool, left in pools.items():
		if left is not None and _num(left) < litres:
			litres, limited_by = _num(left), pool
	if math.isinf(litres):
		return litres, limited_by
	return max(0, math.floor(litres + 1e-9)), limited_by


def litres_for(amount, rate):
	"""Litres an amount buys at a rate; nothing when there is no rate to price it with."""
	return _num(amount) / _num(rate) if _num(rate) > 0 else 0.0


def so_line_litres(line):
	"""Litres left on a Sales Order line as erp-functions' remainingLitres counts them: ordered - delivered
	- in draft Delivery Notes + returned, in the line's unit, times its conversion factor."""
	get = line.get if isinstance(line, dict) else lambda k: getattr(line, k, None)
	left = (
		_num(get("qty"))
		- _num(get("delivered_qty"))
		- _num(get("custom_delivery_note_qty_in_draft"))
		+ _num(get("returned_qty"))
	)
	return left * (_num(get("conversion_factor")) or 1.0)
