"""Merchant identity, settings and the policy they produce.

The merchant row is the authority for guardrails at runtime. Environment
variables seed it on first boot, and the effective policy is always the
*tighter* of the two so a merchant can never widen limits past the deployment's
configured ceiling by editing a form.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..domain.money import format_inr
from ..domain.policy import PurchasePolicy
from ..models import Merchant

log = logging.getLogger("services.merchant")

DEFAULT_POLICIES = {
    "returns": "7-day returns on unopened items; refunds are issued to the original payment method.",
    "shipping": "Free delivery across India on orders above ₹499. Dispatched in 1–2 business days.",
    "warranty": "Manufacturer warranty applies; duration varies by product and is listed per item.",
    "agent_purchases": ("Automated buyers may browse, price and prepare an order, but a "
                        "human must explicitly approve the final amount before any charge."),
    "cancellation": "Orders can be cancelled any time before dispatch.",
}


def get_merchant(db: Session) -> Merchant:
    """Return the single merchant row, creating it from env defaults if absent."""
    merchant = db.scalar(select(Merchant).limit(1))
    if merchant is None:
        merchant = Merchant(
            name="Nova Electronics",
            description=("An independent electronics retailer in Bengaluru selling "
                         "laptops, phones, cameras, audio gear and accessories, with a "
                         "machine-readable catalog for automated buyers."),
            support_email="support@nova-electronics.example",
            currency=settings.allowed_currency,
            max_order_value_paise=settings.max_order_value_paise,
            max_discount_percent=settings.max_discount_percent,
            max_items_per_order=settings.max_items_per_order,
            max_quantity_per_line=settings.max_quantity_per_line,
            confirmation_required=settings.require_payment_confirmation,
            upsell_enabled=True,
            cross_sell_enabled=True,
            tax_percent=settings.tax_percent,
            ai_provider_preference=settings.llm_provider,
            policies_json=dict(DEFAULT_POLICIES),
        )
        db.add(merchant)
        db.flush()
        log.info("created default merchant %s", merchant.id)
    return merchant


def build_policy(merchant: Merchant) -> PurchasePolicy:
    """Effective purchase policy: the stricter of merchant row and env ceiling.

    A merchant raising their own limit above the deployment ceiling has no
    effect — the env value still binds.
    """
    return PurchasePolicy(
        max_order_value_paise=min(merchant.max_order_value_paise,
                                  settings.max_order_value_paise),
        min_order_value_paise=100,
        max_items=min(merchant.max_items_per_order, settings.max_items_per_order),
        max_quantity_per_line=min(merchant.max_quantity_per_line,
                                  settings.max_quantity_per_line),
        allowed_currency=settings.allowed_currency,
        allowed_categories=None,
        requires_confirmation=(merchant.confirmation_required
                               or settings.require_payment_confirmation),
        max_discount_percent=min(merchant.max_discount_percent,
                                 settings.max_discount_percent),
    )


_UPDATABLE = {
    "name", "description", "support_email", "max_order_value_paise",
    "max_discount_percent", "max_items_per_order", "max_quantity_per_line",
    "confirmation_required", "upsell_enabled", "cross_sell_enabled",
    "tax_percent", "ai_provider_preference", "policies_json",
}


def update_settings(db: Session, merchant: Merchant, data: dict) -> tuple[Merchant, list[str]]:
    """Apply merchant settings, clamping anything above the deployment ceiling."""
    clamped: list[str] = []

    if data.get("max_order_value_paise") is not None:
        requested = int(data["max_order_value_paise"])
        if requested > settings.max_order_value_paise:
            clamped.append(
                f"max_order_value clamped from {format_inr(requested)} to the "
                f"deployment ceiling of {format_inr(settings.max_order_value_paise)} "
                f"(MAX_ORDER_VALUE)."
            )
            data["max_order_value_paise"] = settings.max_order_value_paise

    if data.get("max_discount_percent") is not None:
        requested = float(data["max_discount_percent"])
        if requested > settings.max_discount_percent:
            clamped.append(
                f"max_discount_percent clamped from {requested}% to the deployment "
                f"ceiling of {settings.max_discount_percent}% (MAX_DISCOUNT_PERCENT)."
            )
            data["max_discount_percent"] = settings.max_discount_percent

    if data.get("confirmation_required") is False and settings.require_payment_confirmation:
        clamped.append(
            "confirmation_required cannot be turned off: REQUIRE_PAYMENT_CONFIRMATION "
            "is enabled for this deployment."
        )
        data["confirmation_required"] = True

    for key, value in data.items():
        if key in _UPDATABLE and value is not None:
            setattr(merchant, key, value)
    db.flush()
    return merchant, clamped


def merchant_to_dict(merchant: Merchant) -> dict:
    policy = build_policy(merchant)
    return {
        "id": merchant.id,
        "name": merchant.name,
        "description": merchant.description,
        "support_email": merchant.support_email,
        "currency": merchant.currency,
        "settings": {
            "max_order_value_paise": merchant.max_order_value_paise,
            "max_order_value_display": format_inr(merchant.max_order_value_paise),
            "max_discount_percent": merchant.max_discount_percent,
            "max_items_per_order": merchant.max_items_per_order,
            "max_quantity_per_line": merchant.max_quantity_per_line,
            "confirmation_required": merchant.confirmation_required,
            "upsell_enabled": merchant.upsell_enabled,
            "cross_sell_enabled": merchant.cross_sell_enabled,
            "tax_percent": merchant.tax_percent,
            "ai_provider_preference": merchant.ai_provider_preference,
        },
        "effective_policy": policy.to_dict(),
        "deployment_ceilings": {
            "max_order_value_paise": settings.max_order_value_paise,
            "max_discount_percent": settings.max_discount_percent,
            "require_payment_confirmation": settings.require_payment_confirmation,
            "note": ("These come from environment variables and bound whatever the "
                     "merchant sets. Merchant values can only be stricter."),
        },
        "policies": merchant.policies_json or {},
    }


__all__ = ["get_merchant", "build_policy", "update_settings", "merchant_to_dict",
           "DEFAULT_POLICIES"]
