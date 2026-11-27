# Copyright (c) 2026, Fuelbuddy and contributors
# For license information, please see license.txt

"""Overdue from open invoices: unmatched credit pays the oldest invoices first (FIFO). Pure: no database."""

import datetime
from typing import NamedTuple


class OpenInvoice(NamedTuple):
	name: str
	due: datetime.date
	balance: float  # open amount in company currency, > 0


class Overdue(NamedTuple):
	amount: float  # unpaid after credit, on invoices at least grace_days past due
	days: int  # days past due of the oldest invoice still unpaid after credit; 0 when none is past due
	invoices: tuple  # (name, due, unpaid) of the overdue invoices, oldest first


def overdue(invoices, credit, as_of, grace_days):
	"""Overdue as of a date. credit is the account's unmatched credit (>= 0), applied by due date, oldest first.
	An invoice is overdue once as_of is grace_days or more past its due date (BRD FR-7.2). No minimum amount."""
	left = max(0.0, credit)
	unpaid = []
	for inv in sorted(invoices, key=lambda i: (i.due, i.name)):
		used = min(inv.balance, left)
		left -= used
		rest = round(inv.balance - used, 2)
		if rest > 0:
			unpaid.append((inv.name, inv.due, rest))
	late = tuple(u for u in unpaid if (as_of - u[1]).days >= grace_days)
	days = max(0, (as_of - unpaid[0][1]).days) if unpaid else 0
	return Overdue(round(sum(u[2] for u in late), 2), days, late)
