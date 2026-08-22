"""Request/response schemas.

Note what these deliberately do **not** accept: no request model anywhere
carries a price, a line total, an order total, an order status or a payment
status. Those are outputs of the backend, never inputs to it. A client that
sends them is ignored rather than trusted.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
class ProductCreate(StrictModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=300)
    description: str = ""
    category: str = ""
    subcategory: str = ""
    brand: str = ""
    price_paise: int = Field(ge=0, le=100_000_000)
    currency: str = "INR"
    inventory: int = Field(default=0, ge=0, le=1_000_000)
    attributes: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    related_products: list[str] = Field(default_factory=list)
    frequently_bought_together: list[str] = Field(default_factory=list)
    compatible_products: list[str] = Field(default_factory=list)
    active: bool = True


class ProductUpdate(StrictModel):
    sku: str | None = None
    name: str | None = None
    description: str | None = None
    category: str | None = None
    subcategory: str | None = None
    brand: str | None = None
    price_paise: int | None = Field(default=None, ge=0, le=100_000_000)
    currency: str | None = None
    inventory: int | None = Field(default=None, ge=0, le=1_000_000)
    attributes: dict[str, Any] | None = None
    tags: list[str] | None = None
    images: list[str] | None = None
    related_products: list[str] | None = None
    frequently_bought_together: list[str] | None = None
    compatible_products: list[str] | None = None
    active: bool | None = None


# ---------------------------------------------------------------------------
# Sessions / agent
# ---------------------------------------------------------------------------
class SessionCreate(StrictModel):
    actor_type: Literal["human", "ai_agent"] = "human"
    actor_label: str = Field(default="buyer", max_length=120)
    channel: Literal["web", "agent_api", "simulation"] = "web"


class ChatRequest(StrictModel):
    session_id: str
    message: str = Field(min_length=1, max_length=2000)


class SearchRequest(StrictModel):
    query: str = Field(default="", max_length=500)
    category: str | None = None
    brand: str | None = None
    max_price_paise: int | None = Field(default=None, ge=0)
    min_price_paise: int | None = Field(default=None, ge=0)
    in_stock_only: bool = True
    limit: int = Field(default=20, ge=1, le=100)


class RecommendRequest(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    session_id: str | None = None
    limit: int = Field(default=5, ge=1, le=20)


# ---------------------------------------------------------------------------
# Cart — ids and quantities only. Never a price.
# ---------------------------------------------------------------------------
class CartAddRequest(StrictModel):
    session_id: str
    product_id: str
    quantity: int = Field(default=1, ge=1, le=50)
    source: Literal["direct", "upsell", "cross_sell"] = "direct"


class CartQuantityRequest(StrictModel):
    session_id: str
    product_id: str
    quantity: int = Field(ge=0, le=50)


class CartRemoveRequest(StrictModel):
    session_id: str
    product_id: str


# ---------------------------------------------------------------------------
# Checkout / payment
# ---------------------------------------------------------------------------
class PrepareCheckoutRequest(StrictModel):
    session_id: str


class ConfirmPaymentRequest(StrictModel):
    """The human confirmation step.

    `confirmed` must be literally True. There is no default and no coercion
    from a truthy value: an omitted or false flag is refused.
    """
    quote_id: str
    confirmed: bool
    confirmed_by: str = Field(default="buyer", max_length=120)
    idempotency_key: str | None = Field(default=None, max_length=120)


class VerifyPaymentRequest(StrictModel):
    razorpay_order_id: str = Field(max_length=128)
    razorpay_payment_id: str = Field(max_length=128)
    razorpay_signature: str = Field(max_length=256)


class PaymentFailureRequest(StrictModel):
    razorpay_order_id: str = Field(max_length=128)
    reason: str = Field(default="", max_length=1000)


class SandboxPayRequest(StrictModel):
    """Drives the offline sandbox checkout only."""
    provider_order_id: str
    outcome: Literal["success", "failure", "tampered_signature",
                     "authorized_only", "provider_outage"] = "success"
    amount_paise_override: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# Merchant console
# ---------------------------------------------------------------------------
class MerchantSettingsUpdate(StrictModel):
    name: str | None = None
    description: str | None = None
    support_email: str | None = None
    max_order_value_paise: int | None = Field(default=None, ge=100)
    max_discount_percent: float | None = Field(default=None, ge=0, le=100)
    max_items_per_order: int | None = Field(default=None, ge=1, le=100)
    max_quantity_per_line: int | None = Field(default=None, ge=1, le=50)
    confirmation_required: bool | None = None
    upsell_enabled: bool | None = None
    cross_sell_enabled: bool | None = None
    tax_percent: float | None = Field(default=None, ge=0, le=50)
    ai_provider_preference: Literal["auto", "ollama", "groq", "gemini",
                                   "mock", "claude"] | None = None
    policies_json: dict[str, str] | None = None


class CampaignCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    target_segment: str = "all"
    product_ids: list[str] = Field(default_factory=list)
    discount_percent: float = Field(default=0, ge=0, le=100)
    starts_at: str | None = None
    ends_at: str | None = None
    budget_paise: int = Field(default=0, ge=0)
    max_discount_paise_per_order: int = Field(default=0, ge=0)
    status: Literal["DRAFT", "PENDING_APPROVAL"] = "DRAFT"


class CampaignDecision(StrictModel):
    approver: str = Field(default="merchant", max_length=120)
    reason: str = Field(default="", max_length=1000)


class CampaignStatusUpdate(StrictModel):
    status: Literal["DRAFT", "PENDING_APPROVAL", "ACTIVE", "PAUSED", "ENDED", "REJECTED"]
    actor: str = Field(default="merchant", max_length=120)


class SimulationRequest(StrictModel):
    sessions_per_arm: int = Field(default=25, ge=1, le=500)
    seed: int = 1337
    label: str = Field(default="Baseline vs AI-assisted upsell", max_length=200)


class FailureInjectionRequest(StrictModel):
    outage: bool | None = None
    verification_failure: bool | None = None


__all__ = [
    "ProductCreate", "ProductUpdate", "SessionCreate", "ChatRequest",
    "SearchRequest", "RecommendRequest", "CartAddRequest", "CartQuantityRequest",
    "CartRemoveRequest", "PrepareCheckoutRequest", "ConfirmPaymentRequest",
    "VerifyPaymentRequest", "PaymentFailureRequest", "SandboxPayRequest",
    "MerchantSettingsUpdate", "CampaignCreate", "CampaignDecision",
    "CampaignStatusUpdate", "SimulationRequest", "FailureInjectionRequest",
]
