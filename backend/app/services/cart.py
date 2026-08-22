"""Persistent cart.

A cart is an `Order` in status CART. Every mutation re-reads catalog prices and
recomputes the whole total server-side — a client can send product ids and
quantities and nothing else. There is no code path where a request-supplied
price or total reaches the database.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import audit
from ..domain.money import format_inr
from ..domain.pricing import PricedCart, price_cart
from ..domain.states import OrderStatus, transition_order
from ..models import BuyerSession, Order, OrderItem
from . import campaigns as campaign_service
from . import catalog
from .merchant import get_merchant

log = logging.getLogger("services.cart")


class CartError(Exception):
    """A cart operation the buyer should see an explanation for."""

    def __init__(self, message: str, *, code: str = "cart_error", detail: dict | None = None):
        super().__init__(message)
        self.message, self.code, self.detail = message, code, detail or {}


#: Cart states a mutation is allowed to touch. A PAID order is immutable.
_MUTABLE = {OrderStatus.CART.value, OrderStatus.DRAFT.value,
            OrderStatus.CHECKOUT_PENDING.value, OrderStatus.PAYMENT_FAILED.value}


def get_or_create_session(db: Session, session_id: str | None = None, **kwargs) -> BuyerSession:
    if session_id:
        existing = db.get(BuyerSession, session_id)
        if existing:
            return existing
    session = BuyerSession(**kwargs)
    if session_id:
        session.id = session_id
    db.add(session)
    db.flush()
    audit.record(db, audit.Action.SESSION_STARTED, session_id=session.id,
                 actor=session.actor_label, actor_type=session.actor_type,
                 reason=f"New {session.actor_type} session on channel {session.channel}.",
                 is_synthetic=session.is_synthetic)
    return session


def get_active_cart(db: Session, session_id: str) -> Order | None:
    return db.scalar(
        select(Order)
        .where(Order.session_id == session_id, Order.status.in_(sorted(_MUTABLE)))
        .order_by(Order.created_at.desc())
        .limit(1)
    )


def get_or_create_cart(db: Session, session_id: str) -> Order:
    cart = get_active_cart(db, session_id)
    if cart is not None:
        return cart
    session = db.get(BuyerSession, session_id)
    if session is None:
        raise CartError(f"Unknown session {session_id}.", code="session_not_found")
    merchant = get_merchant(db)
    cart = Order(session_id=session_id, status=OrderStatus.CART.value,
                 currency=merchant.currency, is_synthetic=session.is_synthetic)
    db.add(cart)
    db.flush()
    audit.record(db, audit.Action.ORDER_CREATED, session_id=session_id, order_id=cart.id,
                 reason="New cart opened.", is_synthetic=cart.is_synthetic)
    return cart


def _assert_mutable(order: Order) -> None:
    if order.status not in _MUTABLE:
        raise CartError(
            f"This order is {order.status} and can no longer be edited.",
            code="order_not_mutable", detail={"status": order.status},
        )


def add_item(db: Session, session_id: str, product_id: str, quantity: int = 1,
             *, source: str = "direct", actor: str = "buyer",
             actor_type: str = "human") -> Order:
    """Add or increment a line. Price always comes from the catalog row."""
    if quantity < 1:
        raise CartError("Quantity must be at least 1.", code="invalid_quantity")

    cart = get_or_create_cart(db, session_id)
    _assert_mutable(cart)

    product = catalog.get_product(db, product_id)
    if product is None:
        raise CartError(f"No product {product_id} exists in this catalog.",
                        code="product_not_found")
    if not product.active:
        raise CartError(f"{product.name} is no longer available.", code="product_inactive")

    merchant = get_merchant(db)
    existing = next((i for i in cart.items if i.product_id == product_id), None)
    new_qty = (existing.quantity if existing else 0) + quantity

    if new_qty > merchant.max_quantity_per_line:
        raise CartError(
            f"You can order at most {merchant.max_quantity_per_line} of "
            f"{product.name} per order.",
            code="quantity_limit",
            detail={"limit": merchant.max_quantity_per_line, "requested": new_qty},
        )
    if new_qty > product.inventory:
        raise CartError(
            f"Only {product.inventory} × {product.name} left in stock "
            f"(you asked for {new_qty}).",
            code="insufficient_inventory",
            detail={"available": product.inventory, "requested": new_qty},
        )

    if existing:
        existing.quantity = new_qty
        # A line first added as an upsell keeps that attribution.
        if existing.source == "direct" and source != "direct":
            existing.source = source
    else:
        db.add(OrderItem(
            order_id=cart.id, product_id=product.id, sku=product.sku,
            name=product.name, unit_price_paise=product.price_paise,
            quantity=quantity, line_total_paise=product.price_paise * quantity,
            source=source,
        ))
    db.flush()
    db.refresh(cart)

    priced = recalculate(db, cart)
    audit.record(
        db, audit.Action.CART_UPDATED, session_id=session_id, order_id=cart.id,
        actor=actor, actor_type=actor_type,
        reason=(f"Added {quantity} × {product.name} at "
                f"{format_inr(product.price_paise)} (source: {source})."),
        input_data={"product_id": product_id, "quantity": quantity, "source": source},
        amount_paise=priced.total_paise, decision=audit.Decision.ALLOWED,
        is_synthetic=cart.is_synthetic,
    )
    if source in ("upsell", "cross_sell"):
        audit.record(
            db,
            audit.Action.UPSELL_ACCEPTED if source == "upsell"
            else audit.Action.CROSS_SELL_ACCEPTED,
            session_id=session_id, order_id=cart.id, actor=actor, actor_type=actor_type,
            reason=f"Buyer accepted {source} suggestion: {product.name}.",
            amount_paise=product.price_paise * quantity,
            decision=audit.Decision.ALLOWED, is_synthetic=cart.is_synthetic,
        )
    return cart


def set_quantity(db: Session, session_id: str, product_id: str, quantity: int,
                 *, actor: str = "buyer", actor_type: str = "human") -> Order:
    cart = get_or_create_cart(db, session_id)
    _assert_mutable(cart)

    item = next((i for i in cart.items if i.product_id == product_id), None)
    if item is None:
        raise CartError("That item is not in your cart.", code="item_not_in_cart")

    if quantity <= 0:
        return remove_item(db, session_id, product_id, actor=actor, actor_type=actor_type)

    merchant = get_merchant(db)
    product = catalog.get_product(db, product_id)
    if product is None:
        raise CartError("That product no longer exists.", code="product_not_found")
    if quantity > merchant.max_quantity_per_line:
        raise CartError(
            f"You can order at most {merchant.max_quantity_per_line} of this item.",
            code="quantity_limit", detail={"limit": merchant.max_quantity_per_line})
    if quantity > product.inventory:
        raise CartError(
            f"Only {product.inventory} × {product.name} left in stock.",
            code="insufficient_inventory", detail={"available": product.inventory})

    item.quantity = quantity
    db.flush()
    db.refresh(cart)
    priced = recalculate(db, cart)
    audit.record(db, audit.Action.CART_UPDATED, session_id=session_id, order_id=cart.id,
                 actor=actor, actor_type=actor_type,
                 reason=f"Set quantity of {item.name} to {quantity}.",
                 input_data={"product_id": product_id, "quantity": quantity},
                 amount_paise=priced.total_paise, decision=audit.Decision.ALLOWED,
                 is_synthetic=cart.is_synthetic)
    return cart


def remove_item(db: Session, session_id: str, product_id: str,
                *, actor: str = "buyer", actor_type: str = "human") -> Order:
    cart = get_or_create_cart(db, session_id)
    _assert_mutable(cart)
    item = next((i for i in cart.items if i.product_id == product_id), None)
    if item is None:
        raise CartError("That item is not in your cart.", code="item_not_in_cart")
    name = item.name
    db.delete(item)
    db.flush()
    db.refresh(cart)
    priced = recalculate(db, cart)
    audit.record(db, audit.Action.CART_UPDATED, session_id=session_id, order_id=cart.id,
                 actor=actor, actor_type=actor_type, reason=f"Removed {name} from the cart.",
                 input_data={"product_id": product_id},
                 amount_paise=priced.total_paise, decision=audit.Decision.ALLOWED,
                 is_synthetic=cart.is_synthetic)
    return cart


def clear_cart(db: Session, session_id: str, *, actor: str = "buyer",
               actor_type: str = "human") -> Order:
    cart = get_or_create_cart(db, session_id)
    _assert_mutable(cart)
    for item in list(cart.items):
        db.delete(item)
    db.flush()
    db.refresh(cart)
    recalculate(db, cart)
    audit.record(db, audit.Action.CART_CLEARED, session_id=session_id, order_id=cart.id,
                 actor=actor, actor_type=actor_type, reason="Cart cleared.",
                 amount_paise=0, decision=audit.Decision.ALLOWED,
                 is_synthetic=cart.is_synthetic)
    return cart


def recalculate(db: Session, order: Order, *, record_audit: bool = False) -> PricedCart:
    """Recompute the order total from live catalog prices and persist it.

    This is the only writer of the money columns on `orders`.
    """
    merchant = get_merchant(db)
    products = catalog.get_products_by_ids(db, [i.product_id for i in order.items])

    items: list[dict] = []
    for item in order.items:
        product = products.get(item.product_id)
        # Re-snapshot from the catalog so an admin price edit is reflected and
        # a stale line can never undercharge.
        unit = product.price_paise if product else item.unit_price_paise
        item.unit_price_paise = unit
        item.line_total_paise = unit * item.quantity
        items.append({
            "product_id": item.product_id, "sku": item.sku, "name": item.name,
            "unit_price_paise": unit, "quantity": item.quantity,
            "source": item.source,
            "category": product.category if product else "",
        })

    discount_percent, discount_label, campaign_id, max_discount_paise = (
        campaign_service.resolve_discount(db, items, merchant)
    )

    priced = price_cart(
        items,
        tax_percent=merchant.tax_percent,
        discount_percent=discount_percent,
        discount_label=discount_label,
        campaign_id=campaign_id,
        max_discount_paise=max_discount_paise,
        currency=merchant.currency,
    )

    order.subtotal_paise = priced.subtotal_paise
    order.discount_paise = priced.discount_paise
    order.tax_paise = priced.tax_paise
    order.shipping_paise = priced.shipping_paise
    order.total_paise = priced.total_paise
    order.currency = priced.currency
    order.applied_campaign_id = priced.campaign_id
    order.upsell_revenue_paise = priced.upsell_paise
    order.cross_sell_revenue_paise = priced.cross_sell_paise
    db.flush()

    if record_audit:
        audit.record(
            db, audit.Action.PRICE_CALCULATED, session_id=order.session_id,
            order_id=order.id,
            reason=(f"Backend recomputed the total from catalog prices: subtotal "
                    f"{format_inr(priced.subtotal_paise)}, discount "
                    f"{format_inr(priced.discount_paise)}, tax "
                    f"{format_inr(priced.tax_paise)} → total "
                    f"{format_inr(priced.total_paise)}."),
            input_data={"lines": [{"product_id": l.product_id, "qty": l.quantity,
                                   "unit_paise": l.unit_price_paise}
                                  for l in priced.lines]},
            amount_paise=priced.total_paise, decision=audit.Decision.INFO,
            is_synthetic=order.is_synthetic,
        )
    return priced


def empty_cart_view(session_id: str) -> dict:
    """The zero-value cart, in exactly the same shape as a populated one.

    Callers should never have to branch on whether a cart exists yet, and a
    client should never see `discount_paise` appear only sometimes.
    """
    view = price_cart([], currency="INR").to_dict()
    view.update({"order_id": None, "session_id": session_id, "status": None,
                 "is_synthetic": False})
    return view


def cart_view(db: Session, order: Order) -> dict:
    """Full cart payload for the API — always freshly priced."""
    priced = recalculate(db, order)
    products = catalog.get_products_by_ids(db, [i.product_id for i in order.items])
    lines = []
    for line in priced.lines:
        product = products.get(line.product_id)
        entry = line.to_dict()
        entry.update({
            "images": product.images if product else [],
            "brand": product.brand if product else "",
            "inventory": product.inventory if product else 0,
            "in_stock": bool(product and product.inventory >= line.quantity),
            "active": bool(product and product.active),
        })
        lines.append(entry)

    view = priced.to_dict()
    view["lines"] = lines
    view["order_id"] = order.id
    view["session_id"] = order.session_id
    view["status"] = order.status
    view["is_synthetic"] = order.is_synthetic
    return view


def mark_checkout_pending(db: Session, order: Order) -> Order:
    order.status = transition_order(order.status, OrderStatus.CHECKOUT_PENDING).value
    db.flush()
    return order


__all__ = ["CartError", "get_or_create_session", "get_active_cart", "get_or_create_cart",
           "add_item", "set_quantity", "remove_item", "clear_cart", "recalculate",
           "cart_view", "empty_cart_view", "mark_checkout_pending"]
