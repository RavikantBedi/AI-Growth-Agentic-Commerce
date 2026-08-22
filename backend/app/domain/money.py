"""Money handling.

Every amount in this system is an **integer number of paise**. Floats are never
used for money. Razorpay's Orders API also expects the smallest currency unit,
so paise is the natural internal representation and no conversion rounding can
sneak in between our ledger and the payment provider.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def rupees_to_paise(rupees: float | int | str | Decimal) -> int:
    """Convert a rupee amount to paise, rounding half-up at the paisa."""
    d = Decimal(str(rupees))
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def paise_to_rupees(paise: int) -> Decimal:
    """Exact rupee value of a paise amount (Decimal, never float)."""
    return (Decimal(int(paise)) / 100).quantize(Decimal("0.01"))


def format_inr(paise: int) -> str:
    """Human-readable INR with Indian digit grouping, e.g. ₹1,20,450.00."""
    negative = paise < 0
    whole, frac = divmod(abs(int(paise)), 100)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"{'-' if negative else ''}₹{s}.{frac:02d}"


def percent_of(paise: int, percent: float) -> int:
    """`percent` of `paise`, rounded half-up to a whole paisa."""
    d = (Decimal(int(paise)) * Decimal(str(percent)) / Decimal(100))
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


__all__ = ["rupees_to_paise", "paise_to_rupees", "format_inr", "percent_of"]
