"""Buyer-facing API for the conversational commerce interface."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import agent
from ..ai.provider import provider_status
from ..db import get_db
from ..domain.money import format_inr
from ..models import ConversationMessage
from ..schemas import (CartAddRequest, CartQuantityRequest, CartRemoveRequest,
                       ChatRequest, SessionCreate)
from ..services import cart as cart_service
from ..services import catalog as catalog_service
from ..services.merchant import get_merchant
from .deps import http_from_cart_error, request_id

router = APIRouter(prefix="/api/buyer", tags=["buyer"])


@router.post("/session", summary="Start a buyer session")
def start_session(payload: SessionCreate, db: Session = Depends(get_db)):
    session = cart_service.get_or_create_session(
        db, actor_type=payload.actor_type, actor_label=payload.actor_label,
        channel=payload.channel)
    merchant = get_merchant(db)
    db.commit()
    return {
        "session_id": session.id,
        "merchant": {"name": merchant.name, "description": merchant.description,
                     "currency": merchant.currency},
        "ai": provider_status(),
        "greeting": (f"Hi — I'm the {merchant.name} shopping assistant. Tell me what "
                     "you're looking for and your budget, and I'll search the catalog. "
                     "Nothing is ever charged without you approving the exact amount."),
    }


@router.post("/chat", summary="Send a message to the shopping agent")
def chat(payload: ChatRequest, request: Request, db: Session = Depends(get_db)):
    try:
        return agent.handle_turn(db, payload.session_id, payload.message,
                                 request_id=request_id(request))
    except ValueError as exc:
        raise HTTPException(404, detail={"error": "session_not_found",
                                         "message": str(exc)}) from exc


@router.get("/history/{session_id}", summary="Conversation history")
def history(session_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.session_id == session_id)
        .order_by(ConversationMessage.created_at)
    ).all()
    return {"session_id": session_id,
            "messages": [{"role": m.role, "content": m.content, "payload": m.payload,
                          "created_at": m.created_at.isoformat()} for m in rows]}


@router.get("/cart/{session_id}", summary="Current cart, priced by the backend")
def get_cart(session_id: str, db: Session = Depends(get_db)):
    order = cart_service.get_active_cart(db, session_id)
    if order is None:
        return cart_service.empty_cart_view(session_id)
    view = cart_service.cart_view(db, order)
    db.commit()
    return view


@router.post("/cart/add", summary="Add to cart")
def cart_add(payload: CartAddRequest, db: Session = Depends(get_db)):
    try:
        order = cart_service.add_item(db, payload.session_id, payload.product_id,
                                      payload.quantity, source=payload.source)
    except cart_service.CartError as exc:
        db.commit()
        raise http_from_cart_error(exc) from exc
    view = cart_service.cart_view(db, order)
    db.commit()
    return view


@router.post("/cart/quantity", summary="Set line quantity")
def cart_quantity(payload: CartQuantityRequest, db: Session = Depends(get_db)):
    try:
        order = cart_service.set_quantity(db, payload.session_id, payload.product_id,
                                          payload.quantity)
    except cart_service.CartError as exc:
        db.commit()
        raise http_from_cart_error(exc) from exc
    view = cart_service.cart_view(db, order)
    db.commit()
    return view


@router.post("/cart/remove", summary="Remove a line")
def cart_remove(payload: CartRemoveRequest, db: Session = Depends(get_db)):
    try:
        order = cart_service.remove_item(db, payload.session_id, payload.product_id)
    except cart_service.CartError as exc:
        db.commit()
        raise http_from_cart_error(exc) from exc
    view = cart_service.cart_view(db, order)
    db.commit()
    return view


@router.post("/cart/clear/{session_id}", summary="Empty the cart")
def cart_clear(session_id: str, db: Session = Depends(get_db)):
    try:
        order = cart_service.clear_cart(db, session_id)
    except cart_service.CartError as exc:
        db.commit()
        raise http_from_cart_error(exc) from exc
    view = cart_service.cart_view(db, order)
    db.commit()
    return view


@router.get("/products", summary="Browse the catalog")
def browse(db: Session = Depends(get_db), category: str | None = None,
           limit: int = 60, offset: int = 0):
    products, total = catalog_service.list_products(
        db, category=category, limit=min(limit, 200), offset=offset)
    return {"products": [catalog_service.product_to_dict(p) for p in products],
            "total": total, "categories": catalog_service.categories(db)}


__all__ = ["router"]
