"""SQLAlchemy models.

Money columns are integer paise and are named `*_paise` without exception, so
a float can never be mistaken for an amount at a call site.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .domain.states import OrderStatus, PaymentStatus, VerificationStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Merchant
# ---------------------------------------------------------------------------
class Merchant(Base, TimestampMixin):
    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                    default=lambda: _uid("mch"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    support_email: Mapped[str] = mapped_column(String(200), default="")
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    # Merchant-controlled guardrails. The AI reads these; it cannot write them.
    max_order_value_paise: Mapped[int] = mapped_column(Integer, default=10_000_000)
    max_discount_percent: Mapped[int] = mapped_column(Integer, default=20)
    max_items_per_order: Mapped[int] = mapped_column(Integer, default=20)
    max_quantity_per_line: Mapped[int] = mapped_column(Integer, default=5)
    confirmation_required: Mapped[bool] = mapped_column(Boolean, default=True)
    upsell_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cross_sell_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tax_percent: Mapped[float] = mapped_column(Float, default=18.0)
    ai_provider_preference: Mapped[str] = mapped_column(String(32), default="auto")

    policies_json: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
class Product(Base, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_category_active", "category", "active"),
        Index("ix_products_price", "price_paise"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                    default=lambda: _uid("prd"))
    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(100), default="", index=True)
    subcategory: Mapped[str] = mapped_column(String(100), default="")
    brand: Mapped[str] = mapped_column(String(100), default="", index=True)

    price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    inventory: Mapped[int] = mapped_column(Integer, default=0)

    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    images: Mapped[list] = mapped_column(JSON, default=list)

    # Curated relationships. Recommendations may only draw from these +
    # catalog retrieval; the LLM is never allowed to invent compatibility.
    related_products: Mapped[list] = mapped_column(JSON, default=list)
    frequently_bought_together: Mapped[list] = mapped_column(JSON, default=list)
    compatible_products: Mapped[list] = mapped_column(JSON, default=list)

    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


# ---------------------------------------------------------------------------
# Sessions / carts / orders
# ---------------------------------------------------------------------------
class BuyerSession(Base, TimestampMixin):
    __tablename__ = "buyer_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                    default=lambda: _uid("ses"))
    actor_type: Mapped[str] = mapped_column(String(32), default="human")  # human | ai_agent
    actor_label: Mapped[str] = mapped_column(String(120), default="buyer")
    channel: Mapped[str] = mapped_column(String(32), default="web")       # web | agent_api | simulation
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    variant: Mapped[str] = mapped_column(String(32), default="ai_assisted")  # baseline | ai_assisted
    experiment_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    orders: Mapped[list["Order"]] = relationship(back_populates="session")
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="session", order_by="ConversationMessage.created_at"
    )


class ConversationMessage(Base, TimestampMixin):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("buyer_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | agent
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    session: Mapped[BuyerSession] = relationship(back_populates="messages")


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                    default=lambda: _uid("ord"))
    session_id: Mapped[str] = mapped_column(ForeignKey("buyer_sessions.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=OrderStatus.CART.value, index=True)

    # All authoritative, all computed server-side in services/pricing.py.
    subtotal_paise: Mapped[int] = mapped_column(Integer, default=0)
    discount_paise: Mapped[int] = mapped_column(Integer, default=0)
    tax_paise: Mapped[int] = mapped_column(Integer, default=0)
    shipping_paise: Mapped[int] = mapped_column(Integer, default=0)
    total_paise: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    applied_campaign_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    payment_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payment_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # Attribution for growth metrics: which revenue came from the upsell engine.
    upsell_revenue_paise: Mapped[int] = mapped_column(Integer, default=0)
    cross_sell_revenue_paise: Mapped[int] = mapped_column(Integer, default=0)

    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[dict] = mapped_column(JSON, default=dict)

    session: Mapped[BuyerSession] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="order")


class OrderItem(Base, TimestampMixin):
    __tablename__ = "order_items"
    __table_args__ = (UniqueConstraint("order_id", "product_id", name="uq_order_product"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))

    # Snapshot at add-time so a later catalog price edit cannot silently
    # re-price a cart the buyer already saw.
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(300))
    unit_price_paise: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    line_total_paise: Mapped[int] = mapped_column(Integer, default=0)

    #: How this line entered the cart — drives upsell/cross-sell attribution.
    source: Mapped[str] = mapped_column(String(32), default="direct")  # direct|upsell|cross_sell

    order: Mapped[Order] = relationship(back_populates="items")


# ---------------------------------------------------------------------------
# Checkout quote — the bridge between "AI proposed" and "user paid"
# ---------------------------------------------------------------------------
class CheckoutQuote(Base, TimestampMixin):
    """An immutable, server-signed price quote the user is asked to approve.

    The payment step accepts a quote id, not an amount. If anything about the
    cart changed after the quote was produced, the fingerprint no longer
    matches and the payment is refused rather than silently re-priced.
    """
    __tablename__ = "checkout_quotes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                    default=lambda: _uid("qte"))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)

    subtotal_paise: Mapped[int] = mapped_column(Integer)
    discount_paise: Mapped[int] = mapped_column(Integer)
    tax_paise: Mapped[int] = mapped_column(Integer)
    shipping_paise: Mapped[int] = mapped_column(Integer)
    total_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    cart_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    policy_result: Mapped[dict] = mapped_column(JSON, default=dict)
    explanation: Mapped[str] = mapped_column(Text, default="")

    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                    default=lambda: _uid("txn"))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)

    provider: Mapped[str] = mapped_column(String(32))
    provider_order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    provider_signature_present: Mapped[bool] = mapped_column(Boolean, default=False)

    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    status: Mapped[str] = mapped_column(String(32), default=PaymentStatus.CREATED.value, index=True)
    verification_status: Mapped[str] = mapped_column(
        String(32), default=VerificationStatus.NOT_ATTEMPTED.value
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_test_mode: Mapped[bool] = mapped_column(Boolean, default=True)

    #: Non-sensitive provider echo only. Card/UPI credentials are never stored.
    provider_meta: Mapped[dict] = mapped_column(JSON, default=dict)

    order: Mapped[Order] = relationship(back_populates="transactions")


class IdempotencyRecord(Base, TimestampMixin):
    """Server-side dedupe for money-creating requests."""
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="in_progress")  # in_progress|done


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------
class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                    default=lambda: _uid("cmp"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    target_segment: Mapped[str] = mapped_column(String(120), default="all")
    product_ids: Mapped[list] = mapped_column(JSON, default=list)
    discount_percent: Mapped[float] = mapped_column(Float, default=0.0)

    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    budget_paise: Mapped[int] = mapped_column(Integer, default=0)
    spent_paise: Mapped[int] = mapped_column(Integer, default=0)
    max_discount_paise_per_order: Mapped[int] = mapped_column(Integer, default=0)

    #: DRAFT -> PENDING_APPROVAL -> ACTIVE -> PAUSED/ENDED. The AI may only
    #: ever create a campaign in PENDING_APPROVAL; a merchant activates it.
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    created_by: Mapped[str] = mapped_column(String(32), default="merchant")  # merchant | ai_agent
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ai_rationale: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_session_time", "session_id", "created_at"),
        Index("ix_audit_action_time", "action", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    action: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    actor_type: Mapped[str] = mapped_column(String(32), default="system")

    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    reason: Mapped[str] = mapped_column(Text, default="")
    input_data: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[str] = mapped_column(String(32), default="")     # ALLOWED|REJECTED|INFO
    policy_result: Mapped[dict] = mapped_column(JSON, default=dict)
    payment_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amount_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="")

    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ExperimentRun(Base, TimestampMixin):
    """A synthetic growth experiment: baseline vs AI-assisted."""
    __tablename__ = "experiment_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True,
                                    default=lambda: _uid("exp"))
    label: Mapped[str] = mapped_column(String(200), default="Growth simulation")
    sessions_per_arm: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


__all__ = [
    "Merchant", "Product", "BuyerSession", "ConversationMessage", "Order",
    "OrderItem", "CheckoutQuote", "Transaction", "IdempotencyRecord",
    "Campaign", "AuditEvent", "ExperimentRun", "utcnow",
]
