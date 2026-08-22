"""Purchase policy engine.

`evaluate()` runs before every money action and returns a structured verdict
that is shown to the user, stored on the quote, and written to the audit trail.
It is deliberately a pure function over explicit inputs — no database access,
no LLM, no network — so its behaviour is fully testable and cannot be talked
out of a decision by anything an LLM or a product description says.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .money import format_inr


class Rule:
    MAX_ORDER_VALUE = "max_order_value"
    MIN_ORDER_VALUE = "min_order_value"
    MAX_ITEMS = "max_items"
    MAX_QUANTITY_PER_LINE = "max_quantity_per_line"
    ALLOWED_CURRENCY = "allowed_currency"
    ALLOWED_CATEGORIES = "allowed_categories"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    INVENTORY_AVAILABLE = "inventory_available"
    MAX_DISCOUNT_PERCENT = "max_discount_percent"
    PRICE_INTEGRITY = "price_integrity"
    ACTIVE_PRODUCTS_ONLY = "active_products_only"


@dataclass(frozen=True)
class PurchasePolicy:
    """Merchant-controlled limits. The AI reads these but can never widen them."""
    max_order_value_paise: int = 10_000_000
    min_order_value_paise: int = 100
    max_items: int = 20
    max_quantity_per_line: int = 5
    allowed_currency: str = "INR"
    allowed_categories: tuple[str, ...] | None = None  # None = all
    requires_confirmation: bool = True
    max_discount_percent: float = 20.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_order_value_paise": self.max_order_value_paise,
            "max_order_value_display": format_inr(self.max_order_value_paise),
            "min_order_value_paise": self.min_order_value_paise,
            "max_items": self.max_items,
            "max_quantity_per_line": self.max_quantity_per_line,
            "allowed_currency": self.allowed_currency,
            "allowed_categories": list(self.allowed_categories) if self.allowed_categories else "all",
            "requires_confirmation": self.requires_confirmation,
            "max_discount_percent": self.max_discount_percent,
        }


@dataclass
class RuleResult:
    rule: str
    passed: bool
    message: str
    limit: Any = None
    observed: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "passed": self.passed,
            "message": self.message,
            "limit": self.limit,
            "observed": self.observed,
        }


@dataclass
class PolicyResult:
    allowed: bool
    checks: list[RuleResult] = field(default_factory=list)
    requires_confirmation: bool = True
    policy_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def violations(self) -> list[RuleResult]:
        return [c for c in self.checks if not c.passed]

    @property
    def summary(self) -> str:
        if self.allowed:
            return f"All {len(self.checks)} policy checks passed."
        return "; ".join(v.message for v in self.violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "requires_confirmation": self.requires_confirmation,
            "summary": self.summary,
            "checks": [c.to_dict() for c in self.checks],
            "violations": [c.to_dict() for c in self.violations],
            "policy": self.policy_snapshot,
        }


@dataclass
class CartLineView:
    """Minimal, already-authoritative view of a cart line for policy checks."""
    product_id: str
    name: str
    category: str
    unit_price_paise: int
    quantity: int
    inventory_available: int
    active: bool
    catalog_price_paise: int


def evaluate(
    policy: PurchasePolicy,
    lines: list[CartLineView],
    *,
    total_paise: int,
    currency: str,
    discount_percent: float = 0.0,
) -> PolicyResult:
    """Run every rule and return the full verdict (not just the first failure)."""
    checks: list[RuleResult] = []

    checks.append(RuleResult(
        Rule.MAX_ORDER_VALUE,
        total_paise <= policy.max_order_value_paise,
        (f"Order total {format_inr(total_paise)} is within the "
         f"{format_inr(policy.max_order_value_paise)} limit."
         if total_paise <= policy.max_order_value_paise else
         f"Order total {format_inr(total_paise)} exceeds the merchant's maximum "
         f"order value of {format_inr(policy.max_order_value_paise)}."),
        limit=policy.max_order_value_paise, observed=total_paise,
    ))

    checks.append(RuleResult(
        Rule.MIN_ORDER_VALUE,
        total_paise >= policy.min_order_value_paise,
        (f"Order total meets the {format_inr(policy.min_order_value_paise)} minimum."
         if total_paise >= policy.min_order_value_paise else
         f"Order total {format_inr(total_paise)} is below the minimum "
         f"chargeable amount of {format_inr(policy.min_order_value_paise)}."),
        limit=policy.min_order_value_paise, observed=total_paise,
    ))

    item_count = sum(l.quantity for l in lines)
    checks.append(RuleResult(
        Rule.MAX_ITEMS,
        item_count <= policy.max_items,
        (f"{item_count} item(s), within the limit of {policy.max_items}."
         if item_count <= policy.max_items else
         f"Cart has {item_count} items, exceeding the {policy.max_items}-item limit."),
        limit=policy.max_items, observed=item_count,
    ))

    over_qty = [l for l in lines if l.quantity > policy.max_quantity_per_line]
    checks.append(RuleResult(
        Rule.MAX_QUANTITY_PER_LINE,
        not over_qty,
        ("Per-line quantities are within limits."
         if not over_qty else
         "Quantity too high for: " + ", ".join(
             f"{l.name} ({l.quantity} > {policy.max_quantity_per_line})" for l in over_qty)),
        limit=policy.max_quantity_per_line,
        observed=max((l.quantity for l in lines), default=0),
    ))

    checks.append(RuleResult(
        Rule.ALLOWED_CURRENCY,
        currency == policy.allowed_currency,
        (f"Currency {currency} is accepted."
         if currency == policy.allowed_currency else
         f"Currency {currency} is not accepted; this merchant settles in "
         f"{policy.allowed_currency} only."),
        limit=policy.allowed_currency, observed=currency,
    ))

    if policy.allowed_categories:
        bad = [l for l in lines if l.category not in policy.allowed_categories]
        checks.append(RuleResult(
            Rule.ALLOWED_CATEGORIES, not bad,
            ("All items are in permitted categories."
             if not bad else
             "Not purchasable via the agent channel: " + ", ".join(l.name for l in bad)),
            limit=list(policy.allowed_categories),
            observed=sorted({l.category for l in lines}),
        ))

    inactive = [l for l in lines if not l.active]
    checks.append(RuleResult(
        Rule.ACTIVE_PRODUCTS_ONLY, not inactive,
        ("All items are active in the catalog."
         if not inactive else
         "No longer available: " + ", ".join(l.name for l in inactive)),
        observed=[l.product_id for l in inactive],
    ))

    short = [l for l in lines if l.quantity > l.inventory_available]
    checks.append(RuleResult(
        Rule.INVENTORY_AVAILABLE, not short,
        ("All items are in stock in the requested quantities."
         if not short else
         "Insufficient stock: " + ", ".join(
             f"{l.name} (requested {l.quantity}, available {l.inventory_available})"
             for l in short)),
        observed=[{"product_id": l.product_id, "requested": l.quantity,
                   "available": l.inventory_available} for l in short],
    ))

    # Price integrity: the price we are about to charge must equal the current
    # catalog price. Guards against a tampered client and against a stale quote.
    mismatched = [l for l in lines if l.unit_price_paise != l.catalog_price_paise]
    checks.append(RuleResult(
        Rule.PRICE_INTEGRITY, not mismatched,
        ("Every line is priced from the live catalog."
         if not mismatched else
         "Price mismatch against catalog for: " + ", ".join(
             f"{l.name} (cart {format_inr(l.unit_price_paise)} vs catalog "
             f"{format_inr(l.catalog_price_paise)})" for l in mismatched)),
        observed=[{"product_id": l.product_id, "cart": l.unit_price_paise,
                   "catalog": l.catalog_price_paise} for l in mismatched],
    ))

    checks.append(RuleResult(
        Rule.MAX_DISCOUNT_PERCENT,
        discount_percent <= policy.max_discount_percent,
        (f"Discount {discount_percent:.2f}% is within the merchant cap of "
         f"{policy.max_discount_percent:.2f}%."
         if discount_percent <= policy.max_discount_percent else
         f"Discount {discount_percent:.2f}% exceeds the merchant cap of "
         f"{policy.max_discount_percent:.2f}% and was rejected."),
        limit=policy.max_discount_percent, observed=discount_percent,
    ))

    checks.append(RuleResult(
        Rule.REQUIRES_CONFIRMATION, True,
        ("Explicit user confirmation is required before any charge."
         if policy.requires_confirmation else
         "Merchant has disabled the confirmation requirement."),
        limit=policy.requires_confirmation, observed=policy.requires_confirmation,
    ))

    return PolicyResult(
        allowed=all(c.passed for c in checks),
        checks=checks,
        requires_confirmation=policy.requires_confirmation,
        policy_snapshot=policy.to_dict(),
    )


def clamp_discount(requested_percent: float, policy: PurchasePolicy) -> tuple[float, bool, str]:
    """Clamp a requested discount to the merchant cap.

    Returns (effective_percent, was_clamped, explanation). Used by the campaign
    recommender so an AI-proposed 35% against a 20% cap is visibly rejected and
    reduced rather than quietly applied.
    """
    if requested_percent < 0:
        return 0.0, True, "Negative discounts are not permitted; clamped to 0%."
    if requested_percent <= policy.max_discount_percent:
        return requested_percent, False, (
            f"Requested {requested_percent:.2f}% is within the "
            f"{policy.max_discount_percent:.2f}% merchant cap."
        )
    return policy.max_discount_percent, True, (
        f"REJECTED: requested {requested_percent:.2f}% exceeds the merchant's "
        f"maximum of {policy.max_discount_percent:.2f}%. Clamped to "
        f"{policy.max_discount_percent:.2f}%."
    )


__all__ = ["Rule", "PurchasePolicy", "RuleResult", "PolicyResult", "CartLineView",
           "evaluate", "clamp_discount"]
