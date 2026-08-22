"""Authoritative price calculation.

The frontend never supplies a price, a line total, or an order total. It sends
product ids and quantities; everything monetary is computed here from the live
catalog row. `price_cart` is the single place order money is produced.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from .money import format_inr, percent_of


@dataclass
class PricedLine:
    product_id: str
    sku: str
    name: str
    unit_price_paise: int
    quantity: int
    line_total_paise: int
    source: str = "direct"
    category: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["unit_price_display"] = format_inr(self.unit_price_paise)
        d["line_total_display"] = format_inr(self.line_total_paise)
        return d


@dataclass
class PricedCart:
    lines: list[PricedLine] = field(default_factory=list)
    subtotal_paise: int = 0
    discount_paise: int = 0
    taxable_paise: int = 0
    tax_paise: int = 0
    shipping_paise: int = 0
    total_paise: int = 0
    currency: str = "INR"
    tax_percent: float = 0.0
    discount_percent: float = 0.0
    discount_label: str = ""
    campaign_id: str | None = None
    upsell_paise: int = 0
    cross_sell_paise: int = 0

    @property
    def item_count(self) -> int:
        return sum(l.quantity for l in self.lines)

    def fingerprint(self) -> str:
        """Stable hash of exactly what would be charged.

        A quote is only redeemable while this matches the live cart, so any
        change to items, quantities, prices, discount or tax invalidates a
        pending payment instead of silently re-pricing it.
        """
        payload = {
            "lines": sorted(
                [[l.product_id, l.unit_price_paise, l.quantity] for l in self.lines]
            ),
            "subtotal": self.subtotal_paise,
            "discount": self.discount_paise,
            "tax": self.tax_paise,
            "shipping": self.shipping_paise,
            "total": self.total_paise,
            "currency": self.currency,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "lines": [l.to_dict() for l in self.lines],
            "item_count": self.item_count,
            "subtotal_paise": self.subtotal_paise,
            "discount_paise": self.discount_paise,
            "taxable_paise": self.taxable_paise,
            "tax_paise": self.tax_paise,
            "shipping_paise": self.shipping_paise,
            "total_paise": self.total_paise,
            "currency": self.currency,
            "tax_percent": self.tax_percent,
            "discount_percent": self.discount_percent,
            "discount_label": self.discount_label,
            "campaign_id": self.campaign_id,
            "upsell_paise": self.upsell_paise,
            "cross_sell_paise": self.cross_sell_paise,
            "subtotal_display": format_inr(self.subtotal_paise),
            "discount_display": format_inr(self.discount_paise),
            "tax_display": format_inr(self.tax_paise),
            "shipping_display": format_inr(self.shipping_paise),
            "total_display": format_inr(self.total_paise),
            "fingerprint": self.fingerprint(),
        }


def price_cart(
    items: list[dict],
    *,
    tax_percent: float = 0.0,
    discount_percent: float = 0.0,
    discount_label: str = "",
    campaign_id: str | None = None,
    max_discount_paise: int | None = None,
    shipping_paise: int = 0,
    currency: str = "INR",
) -> PricedCart:
    """Compute a cart total from authoritative catalog data.

    `items` entries must carry the *catalog* unit price — callers read it from
    the database row, never from a request body.

    Order of operations: line totals -> subtotal -> discount (capped) -> tax on
    the discounted amount -> shipping -> total.
    """
    lines: list[PricedLine] = []
    for it in items:
        qty = int(it["quantity"])
        unit = int(it["unit_price_paise"])
        lines.append(PricedLine(
            product_id=it["product_id"],
            sku=it.get("sku", ""),
            name=it.get("name", ""),
            unit_price_paise=unit,
            quantity=qty,
            line_total_paise=unit * qty,
            source=it.get("source", "direct"),
            category=it.get("category", ""),
        ))

    subtotal = sum(l.line_total_paise for l in lines)

    discount = percent_of(subtotal, discount_percent) if discount_percent > 0 else 0
    if max_discount_paise is not None:
        discount = min(discount, max(0, max_discount_paise))
    discount = min(discount, subtotal)  # never discount below zero

    taxable = subtotal - discount
    tax = percent_of(taxable, tax_percent) if tax_percent > 0 else 0
    total = taxable + tax + shipping_paise

    effective_discount_percent = (
        round(discount * 100 / subtotal, 4) if subtotal > 0 else 0.0
    )

    return PricedCart(
        lines=lines,
        subtotal_paise=subtotal,
        discount_paise=discount,
        taxable_paise=taxable,
        tax_paise=tax,
        shipping_paise=shipping_paise,
        total_paise=total,
        currency=currency,
        tax_percent=tax_percent,
        discount_percent=effective_discount_percent,
        discount_label=discount_label,
        campaign_id=campaign_id,
        upsell_paise=sum(l.line_total_paise for l in lines if l.source == "upsell"),
        cross_sell_paise=sum(l.line_total_paise for l in lines if l.source == "cross_sell"),
    )


__all__ = ["PricedLine", "PricedCart", "price_cart"]
