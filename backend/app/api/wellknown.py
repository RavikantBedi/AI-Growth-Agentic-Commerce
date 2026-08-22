"""Agent-commerce discovery manifest.

`/.well-known/agent-commerce.json` is **this application's own manifest format**.
It is not an implementation of any published agent-commerce standard, and no
compliance with one is claimed. `spec` names the format and version so a client
can tell exactly what it is reading.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..payments import get_payment_provider
from ..services import catalog as catalog_service
from ..services.merchant import build_policy, get_merchant

router = APIRouter(tags=["discovery"])

SPEC_NAME = "nova.agent-commerce"
SPEC_VERSION = "0.1"


@router.get("/.well-known/agent-commerce.json",
            summary="Machine-readable merchant manifest")
def agent_commerce_manifest(db: Session = Depends(get_db)):
    merchant = get_merchant(db)
    policy = build_policy(merchant)
    provider = get_payment_provider()
    _, product_count = catalog_service.list_products(db, active_only=True, limit=1)
    db.commit()

    return {
        "spec": SPEC_NAME,
        "spec_version": SPEC_VERSION,
        "spec_note": ("This is this application's own agent-commerce manifest format. "
                      "It does not implement, and does not claim compliance with, any "
                      "external agent-commerce protocol specification."),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "merchant": {
            "id": merchant.id,
            "name": merchant.name,
            "description": merchant.description,
            "support_email": merchant.support_email,
        },
        "currency": merchant.currency,
        "price_units": "integer paise (1 INR = 100 paise)",
        "catalog": {
            "product_count": product_count,
            "categories": [c["category"] for c in catalog_service.categories(db)],
            "brands": catalog_service.brands(db),
        },
        "capabilities": [
            "catalog_discovery",
            "catalog_search",
            "structured_recommendations",
            "cart_management",
            "bounded_upsell",
            "server_side_pricing",
            "policy_evaluation",
            "checkout_quote",
            "test_payment",
            "payment_verification",
            "audit_trail",
        ],
        "payment_methods": [
            {
                "provider": provider.name,
                "label": provider.display_label,
                "mode": "test",
                "simulated": provider.name == "local_sandbox",
                "flow": ("Backend creates a provider order from an approved quote; the "
                         "payer completes it through the provider's checkout; the "
                         "backend verifies the signature and the payment server-side "
                         "before the order is marked paid."),
            }
        ],
        "policies": {
            "max_order_value_paise": policy.max_order_value_paise,
            "min_order_value_paise": policy.min_order_value_paise,
            "max_items": policy.max_items,
            "max_quantity_per_line": policy.max_quantity_per_line,
            "allowed_currency": policy.allowed_currency,
            "max_discount_percent": policy.max_discount_percent,
            "requires_user_confirmation": True,
            "automated_purchase_without_human_approval": False,
            **{k: v for k, v in (merchant.policies_json or {}).items()},
        },
        "endpoints": {
            "manifest": "/.well-known/agent-commerce.json",
            "catalog": "/api/agent/catalog",
            "products": "/api/agent/products",
            "product": "/api/agent/products/{product_id}",
            "search": "POST /api/agent/search",
            "recommend": "POST /api/agent/recommend",
            "open_session": "POST /api/agent/session",
            "cart_add": "POST /api/agent/cart",
            "cart_remove": "POST /api/agent/cart/remove",
            "cart_get": "/api/agent/cart/{session_id}",
            "upsell": "/api/agent/upsell/{session_id}",
            "checkout_quote": "POST /api/agent/checkout",
            "human_confirmation": "POST /api/payments/confirm",
            "payment_verification": "POST /api/payments/verify",
            "order": "/api/agent/order/{order_id}",
            "capabilities": "/api/agent/capabilities",
            "openapi": "/openapi.json",
            "docs": "/docs",
        },
        "agent_constraints": {
            "agent_may_create_payment": False,
            "agent_may_confirm_payment": False,
            "agent_may_set_prices": False,
            "agent_may_grant_discounts": False,
            "agent_may_activate_campaigns": False,
            "human_confirmation_required_before_charge": True,
            "server_side_payment_verification": True,
            "explanation": ("An automated buyer can discover, price, and prepare an "
                            "order. Creating a charge requires an explicit human "
                            "confirmation of a specific quote, submitted separately. "
                            "POST /api/agent/payment returns 403 by design."),
        },
        "limits": {
            "max_order_value_paise": settings.max_order_value_paise,
            "max_discount_percent": settings.max_discount_percent,
            "catalog_page_size_max": 1000,
        },
        "test_mode": True,
        "test_mode_notice": ("Every payment in this deployment is test mode. No real "
                             "money can be collected."),
    }


__all__ = ["router", "SPEC_NAME", "SPEC_VERSION"]
