"""Bounded upsell and cross-sell.

Suggestions are drawn only from curated catalog relationships on items already
in the cart — `frequently_bought_together`, `compatible_products`,
`related_products`. Compatibility is never inferred by a model.

Every suggestion is bounded before it is shown:

  * at most `MAX_SUGGESTIONS` add-ons per turn
  * each add-on costs at most `MAX_SINGLE_UPSELL_RATIO` of the cart subtotal
  * all suggestions together cost at most `MAX_TOTAL_UPSELL_RATIO` of subtotal
  * the resulting total must stay inside the merchant's max order value
  * must be active and in stock

and carries its incremental cost and the resulting new total, so the shopper
sees the price consequence before agreeing to anything.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..domain.money import format_inr, percent_of
from ..models import Product
from . import catalog

log = logging.getLogger("services.upsell")

MAX_SUGGESTIONS = 3
MAX_SINGLE_UPSELL_RATIO = 0.35   # one add-on may not exceed 35% of the subtotal
MAX_TOTAL_UPSELL_RATIO = 0.50    # all add-ons together may not exceed 50%

#: Relationship -> (kind, weight, phrasing). Ordering here decides precedence.
RELATION_SOURCES: list[tuple[str, str, float, str]] = [
    ("frequently_bought_together", "cross_sell", 1.0,
     "frequently bought together with {anchor}"),
    ("compatible_products", "upsell", 0.85,
     "listed as compatible with {anchor}"),
    ("related_products", "cross_sell", 0.6,
     "related to {anchor} in the catalog"),
]


@dataclass
class Suggestion:
    product: Product
    kind: str                 # upsell | cross_sell
    anchor_product_id: str
    anchor_name: str
    reason: str
    incremental_paise: int
    new_subtotal_paise: int
    new_total_paise: int
    score: float

    def to_dict(self) -> dict:
        return {
            "product_id": self.product.id,
            "sku": self.product.sku,
            "name": self.product.name,
            "brand": self.product.brand,
            "category": self.product.category,
            "price_paise": self.product.price_paise,
            "price_display": format_inr(self.product.price_paise),
            "images": self.product.images or [],
            "inventory": self.product.inventory,
            "kind": self.kind,
            "anchor_product_id": self.anchor_product_id,
            "anchor_name": self.anchor_name,
            "reason": self.reason,
            "incremental_paise": self.incremental_paise,
            "incremental_display": format_inr(self.incremental_paise),
            "new_subtotal_paise": self.new_subtotal_paise,
            "new_subtotal_display": format_inr(self.new_subtotal_paise),
            "new_total_paise": self.new_total_paise,
            "new_total_display": format_inr(self.new_total_paise),
            "score": round(self.score, 2),
        }


@dataclass
class UpsellResult:
    suggestions: list[Suggestion]
    rejected: list[dict]
    base_total_paise: int
    bounds: dict

    def to_dict(self) -> dict:
        return {
            "suggestions": [s.to_dict() for s in self.suggestions],
            "rejected": self.rejected,
            "base_total_paise": self.base_total_paise,
            "base_total_display": format_inr(self.base_total_paise),
            "bounds": self.bounds,
        }


def suggest(
    db: Session,
    cart_items: list[dict],
    *,
    subtotal_paise: int,
    max_order_value_paise: int,
    upsell_enabled: bool = True,
    cross_sell_enabled: bool = True,
    tax_percent: float = 0.0,
    limit: int = MAX_SUGGESTIONS,
) -> UpsellResult:
    """Produce bounded add-on suggestions for the current cart.

    `cart_items` are dicts with at least `product_id`, `name`, `quantity`.
    `tax_percent` matters: the max-order-value bound is checked against the
    projected *charged* total, not the subtotal, so an add-on can never push the
    order past the merchant's limit once tax is applied.
    """
    def with_tax(subtotal: int) -> int:
        return subtotal + percent_of(subtotal, tax_percent) if tax_percent > 0 else subtotal

    bounds = {
        "max_suggestions": limit,
        "max_single_upsell_ratio": MAX_SINGLE_UPSELL_RATIO,
        "max_total_upsell_ratio": MAX_TOTAL_UPSELL_RATIO,
        "max_single_upsell_paise": int(subtotal_paise * MAX_SINGLE_UPSELL_RATIO),
        "max_total_upsell_paise": int(subtotal_paise * MAX_TOTAL_UPSELL_RATIO),
        "max_order_value_paise": max_order_value_paise,
        "tax_percent": tax_percent,
        "projected_total_before_addons_paise": with_tax(subtotal_paise),
        "upsell_enabled": upsell_enabled,
        "cross_sell_enabled": cross_sell_enabled,
    }
    if not cart_items or subtotal_paise <= 0:
        return UpsellResult([], [], subtotal_paise, bounds)
    if not upsell_enabled and not cross_sell_enabled:
        return UpsellResult([], [{"reason": "Upsell and cross-sell are disabled "
                                            "by the merchant."}], subtotal_paise, bounds)

    in_cart = {i["product_id"] for i in cart_items}
    anchors = catalog.get_products_by_ids(db, list(in_cart))

    # Gather candidates from curated relationships only.
    candidates: dict[str, dict] = {}
    for item in cart_items:
        anchor = anchors.get(item["product_id"])
        if anchor is None:
            continue
        for attr, kind, weight, phrasing in RELATION_SOURCES:
            for pid in (getattr(anchor, attr) or [])[:12]:
                if pid in in_cart:
                    continue
                existing = candidates.get(pid)
                if existing and existing["weight"] >= weight:
                    continue
                candidates[pid] = {
                    "kind": kind, "weight": weight,
                    "anchor_id": anchor.id, "anchor_name": anchor.name,
                    "phrasing": phrasing.format(anchor=anchor.name),
                }

    if not candidates:
        return UpsellResult([], [{"reason": "No curated accessory or companion "
                                            "relationships exist for these items."}],
                            subtotal_paise, bounds)

    products = catalog.get_products_by_ids(db, list(candidates))
    accepted: list[Suggestion] = []
    rejected: list[dict] = []
    running_subtotal = subtotal_paise
    running_upsell = 0

    ordered = sorted(
        candidates.items(),
        key=lambda kv: (-kv[1]["weight"], products[kv[0]].price_paise
                        if kv[0] in products else 0),
    )

    for pid, meta in ordered:
        product = products.get(pid)
        if product is None:
            continue

        kind = meta["kind"]
        if kind == "upsell" and not upsell_enabled:
            rejected.append({"product_id": pid, "name": product.name,
                             "reason": "Upsell is disabled by the merchant."})
            continue
        if kind == "cross_sell" and not cross_sell_enabled:
            rejected.append({"product_id": pid, "name": product.name,
                             "reason": "Cross-sell is disabled by the merchant."})
            continue
        if not product.active or product.inventory < 1:
            rejected.append({"product_id": pid, "name": product.name,
                             "reason": "Out of stock or inactive."})
            continue

        price = product.price_paise
        if price > bounds["max_single_upsell_paise"]:
            rejected.append({
                "product_id": pid, "name": product.name,
                "reason": (f"{format_inr(price)} exceeds the per-suggestion cap of "
                           f"{format_inr(bounds['max_single_upsell_paise'])} "
                           f"({int(MAX_SINGLE_UPSELL_RATIO * 100)}% of the cart).")})
            continue
        if running_upsell + price > bounds["max_total_upsell_paise"]:
            rejected.append({
                "product_id": pid, "name": product.name,
                "reason": (f"Would push total add-ons past the "
                           f"{int(MAX_TOTAL_UPSELL_RATIO * 100)}% cap "
                           f"({format_inr(bounds['max_total_upsell_paise'])}).")})
            continue
        projected_total = with_tax(running_subtotal + price)
        if projected_total > max_order_value_paise:
            rejected.append({
                "product_id": pid, "name": product.name,
                "reason": (f"Would bring the charged total to {format_inr(projected_total)} "
                           f"(including {tax_percent:g}% tax), past the merchant's maximum "
                           f"order value of {format_inr(max_order_value_paise)}.")})
            continue

        running_subtotal += price
        running_upsell += price
        accepted.append(Suggestion(
            product=product, kind=kind,
            anchor_product_id=meta["anchor_id"], anchor_name=meta["anchor_name"],
            reason=meta["phrasing"].capitalize() + ".",
            incremental_paise=price, new_subtotal_paise=running_subtotal,
            new_total_paise=projected_total,
            score=meta["weight"] * 100,
        ))
        if len(accepted) >= limit:
            break

    return UpsellResult(accepted, rejected, subtotal_paise, bounds)


def explain(result: UpsellResult) -> str:
    """Plain-language summary the buyer UI shows alongside the suggestions."""
    if not result.suggestions:
        return "No add-ons to suggest for this cart."
    lines = ["Suggested additions, with the exact price impact:"]
    for s in result.suggestions:
        lines.append(
            f"• {s.product.name} — {format_inr(s.incremental_paise)} "
            f"({s.reason}) → new total {format_inr(s.new_total_paise)}"
        )
    lines.append("Nothing is added until you say yes, and nothing is charged "
                 "until you approve the final amount.")
    return "\n".join(lines)


__all__ = ["Suggestion", "UpsellResult", "suggest", "explain",
           "MAX_SUGGESTIONS", "MAX_SINGLE_UPSELL_RATIO", "MAX_TOTAL_UPSELL_RATIO"]
