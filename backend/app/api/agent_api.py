"""Agent-facing commerce API.

These endpoints exist so another AI system can discover this merchant, read the
catalog as structured data, price a cart and prepare a purchase — without
scraping HTML and without any ability to move money on its own.

The boundary is the same one the human UI has: an agent can reach
`/api/agent/checkout`, which returns a priced, policy-checked quote. Turning a
quote into a payment requires the human confirmation endpoint under
`/api/payments`, which is not part of this agent surface.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..ai import tools
from ..config import settings
from ..db import get_db
from ..domain.money import format_inr
from ..models import Order
from ..payments import get_payment_provider
from ..schemas import (CartAddRequest, CartRemoveRequest, PrepareCheckoutRequest,
                       RecommendRequest, SearchRequest, SessionCreate)
from ..services import cart as cart_service
from ..services import catalog as catalog_service
from ..services import checkout as checkout_service
from ..services import recommend as recommend_service
from ..services import upsell as upsell_service
from ..services.merchant import build_policy, get_merchant, merchant_to_dict
from .deps import http_from_cart_error, http_from_checkout_error, request_id

router = APIRouter(prefix="/api/agent", tags=["agent-commerce"])


@router.get("/catalog", summary="Full machine-readable catalog")
def agent_catalog(db: Session = Depends(get_db), limit: int = 500, offset: int = 0):
    """Structured catalog for machine consumption — no HTML, no scraping."""
    merchant = get_merchant(db)
    products, total = catalog_service.list_products(
        db, active_only=True, limit=min(limit, 1000), offset=offset)
    provider = get_payment_provider()
    return {
        "merchant": {
            "id": merchant.id, "name": merchant.name,
            "description": merchant.description,
            "support_email": merchant.support_email, "currency": merchant.currency,
        },
        "products": [catalog_service.product_to_dict(p) for p in products],
        "total_products": total,
        "returned": len(products),
        "offset": offset,
        "categories": catalog_service.categories(db),
        "brands": catalog_service.brands(db),
        "policies": {
            **(merchant.policies_json or {}),
            "purchase": build_policy(merchant).to_dict(),
        },
        "currency": merchant.currency,
        "payment": {"provider": provider.name, "test_mode": provider.is_test_mode,
                    "label": provider.display_label},
        "price_units": "All monetary values are integer paise (1 INR = 100 paise).",
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/products", summary="List products")
def agent_products(db: Session = Depends(get_db), category: str | None = None,
                   brand: str | None = None, in_stock_only: bool = False,
                   min_price_paise: int | None = None,
                   max_price_paise: int | None = None,
                   limit: int = 100, offset: int = 0):
    products, total = catalog_service.list_products(
        db, category=category, brand=brand, in_stock_only=in_stock_only,
        min_price_paise=min_price_paise, max_price_paise=max_price_paise,
        limit=min(limit, 500), offset=offset)
    return {"products": [catalog_service.product_to_dict(p) for p in products],
            "total": total, "limit": limit, "offset": offset}


@router.get("/products/{product_id}", summary="Product detail")
def agent_product(product_id: str, db: Session = Depends(get_db)):
    product = catalog_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(404, detail={"error": "product_not_found",
                                         "message": f"No product {product_id}."})
    return catalog_service.product_to_dict(product)


@router.post("/search", summary="Structured catalog search")
def agent_search(payload: SearchRequest, db: Session = Depends(get_db)):
    results = catalog_service.search_products(
        db, payload.query, category=payload.category,
        max_price_paise=payload.max_price_paise,
        min_price_paise=payload.min_price_paise,
        in_stock_only=payload.in_stock_only, limit=payload.limit)
    return {
        "query": payload.query,
        "count": len(results),
        "results": [{**catalog_service.product_to_dict(p, include_relations=False),
                     "match_score": round(score, 2)} for p, score in results],
    }


@router.post("/recommend", summary="Ranked recommendations with reasoning")
def agent_recommend(payload: RecommendRequest, db: Session = Depends(get_db)):
    """Deterministic ranking with the signals exposed, so an agent can audit it."""
    requirements = recommend_service.extract_requirements(
        payload.query, known_brands=catalog_service.brands(db))
    cart_ids: list[str] = []
    if payload.session_id:
        order = cart_service.get_active_cart(db, payload.session_id)
        cart_ids = [i.product_id for i in order.items] if order else []
    scored = recommend_service.rank_candidates(
        db, requirements, limit=payload.limit, cart_product_ids=cart_ids)
    db.commit()
    return {
        "query": payload.query,
        "extracted_requirements": requirements.to_dict(),
        "ranking_weights": recommend_service.WEIGHTS,
        "recommendations": [s.to_dict() for s in scored],
        "note": ("Ranking is deterministic and computed from catalog data. Any natural "
                 "language explanation is generated separately and cannot alter these "
                 "products, prices or scores."),
    }


@router.post("/session", summary="Open an agent buyer session")
def agent_session(payload: SessionCreate, db: Session = Depends(get_db)):
    session = cart_service.get_or_create_session(
        db, actor_type=payload.actor_type, actor_label=payload.actor_label,
        channel="agent_api")
    db.commit()
    return {"session_id": session.id, "actor_type": session.actor_type,
            "channel": session.channel, "created_at": session.created_at.isoformat()}


@router.post("/cart", summary="Add an item to an agent cart")
def agent_cart_add(payload: CartAddRequest, db: Session = Depends(get_db)):
    try:
        order = cart_service.add_item(
            db, payload.session_id, payload.product_id, payload.quantity,
            source=payload.source, actor="ai_agent", actor_type="ai_agent")
    except cart_service.CartError as exc:
        db.commit()
        raise http_from_cart_error(exc) from exc
    view = cart_service.cart_view(db, order)
    db.commit()
    return view


@router.post("/cart/remove", summary="Remove an item from an agent cart")
def agent_cart_remove(payload: CartRemoveRequest, db: Session = Depends(get_db)):
    try:
        order = cart_service.remove_item(db, payload.session_id, payload.product_id,
                                         actor="ai_agent", actor_type="ai_agent")
    except cart_service.CartError as exc:
        db.commit()
        raise http_from_cart_error(exc) from exc
    view = cart_service.cart_view(db, order)
    db.commit()
    return view


@router.get("/cart/{session_id}", summary="Backend-calculated cart")
def agent_cart_get(session_id: str, db: Session = Depends(get_db)):
    order = cart_service.get_active_cart(db, session_id)
    if order is None:
        return cart_service.empty_cart_view(session_id)
    view = cart_service.cart_view(db, order)
    db.commit()
    return view


@router.get("/upsell/{session_id}", summary="Bounded add-on suggestions")
def agent_upsell(session_id: str, db: Session = Depends(get_db)):
    order = cart_service.get_active_cart(db, session_id)
    if order is None or not order.items:
        return {"suggestions": [], "rejected": [], "bounds": {}}
    merchant = get_merchant(db)
    priced = cart_service.recalculate(db, order)
    result = upsell_service.suggest(
        db, [{"product_id": i.product_id, "name": i.name, "quantity": i.quantity}
             for i in order.items],
        subtotal_paise=priced.subtotal_paise,
        max_order_value_paise=merchant.max_order_value_paise,
        upsell_enabled=merchant.upsell_enabled,
        cross_sell_enabled=merchant.cross_sell_enabled,
        tax_percent=merchant.tax_percent)
    db.commit()
    return {**result.to_dict(), "explanation": upsell_service.explain(result)}


@router.post("/checkout", summary="Price and policy-check a cart (creates no payment)")
def agent_checkout(payload: PrepareCheckoutRequest, request: Request,
                   db: Session = Depends(get_db)):
    """Produce a quote for a human to approve.

    This endpoint never creates a payment. The response tells the calling agent
    exactly where the human confirmation must happen.
    """
    try:
        result = checkout_service.prepare_checkout(
            db, payload.session_id, actor="ai_agent", actor_type="ai_agent",
            request_id=request_id(request))
    except checkout_service.CheckoutError as exc:
        db.commit()
        raise http_from_checkout_error(exc) from exc
    db.commit()
    result["human_confirmation_required"] = {
        "endpoint": "POST /api/payments/confirm",
        "body": {"quote_id": result["quote_id"], "confirmed": True},
        "note": ("An automated agent cannot supply this confirmation on the user's "
                 "behalf. It must come from the person paying, after they have seen "
                 "the amount in `explanation`."),
    }
    return result


@router.post("/payment", summary="Not available to agents — by design", status_code=403)
def agent_payment_blocked():
    """Explicitly refuses. Present so the boundary is discoverable, not hidden."""
    raise HTTPException(403, detail={
        "error": "payment_requires_human_confirmation",
        "message": ("An AI agent cannot create a payment. Prepare a quote with "
                    "POST /api/agent/checkout, present the amount to the person "
                    "paying, and have them confirm it via POST /api/payments/confirm."),
        "boundary": tools.describe()["money_boundary"],
        "forbidden_capabilities": tools.FORBIDDEN_CAPABILITIES,
    })


@router.get("/order/{order_id}", summary="Order status")
def agent_order(order_id: str, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, detail={"error": "order_not_found",
                                         "message": f"No order {order_id}."})
    latest = sorted(order.transactions, key=lambda t: t.created_at)[-1] if order.transactions else None
    return {
        "order_id": order.id,
        "session_id": order.session_id,
        "status": order.status,
        "paid": order.status == "PAID",
        "items": [{"product_id": i.product_id, "sku": i.sku, "name": i.name,
                   "quantity": i.quantity, "unit_price_paise": i.unit_price_paise,
                   "line_total_paise": i.line_total_paise, "source": i.source}
                  for i in order.items],
        "subtotal_paise": order.subtotal_paise,
        "discount_paise": order.discount_paise,
        "tax_paise": order.tax_paise,
        "total_paise": order.total_paise,
        "total_display": format_inr(order.total_paise),
        "currency": order.currency,
        "payment_provider": order.payment_provider,
        "payment_order_id": order.payment_order_id,
        "payment_id": order.payment_id,
        "payment_status": latest.status if latest else None,
        "verification_status": latest.verification_status if latest else None,
        "is_test_mode": latest.is_test_mode if latest else True,
        "is_synthetic": order.is_synthetic,
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat(),
    }


@router.get("/capabilities", summary="What this agent surface can and cannot do")
def agent_capabilities(db: Session = Depends(get_db)):
    merchant = get_merchant(db)
    return {
        "merchant": merchant_to_dict(merchant),
        "tools": tools.describe(),
        "guardrails": {
            "max_order_value_paise": settings.max_order_value_paise,
            "max_discount_percent": settings.max_discount_percent,
            "requires_human_confirmation": settings.require_payment_confirmation,
        },
    }


__all__ = ["router"]
