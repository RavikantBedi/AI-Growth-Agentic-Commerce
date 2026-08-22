"""Tool permissions for the AI agent.

The agent can only affect the world through this registry. It is declarative so
the merchant console can render exactly what the AI is and is not allowed to do,
and so the boundary is testable: `test_ai_cannot_call_payment` asserts that no
money-moving capability is reachable from here.

The hard rule:

    AI -> prepare_checkout / request_payment_confirmation   (proposals only)
                    |
             policy engine + explicit human confirmation
                    |
            backend payment service -> Razorpay

There is deliberately no `create_payment`, `capture_payment`, `refund`,
`set_price`, `apply_discount` or `activate_campaign` tool. Those functions exist
in the codebase but are not addressable from an LLM turn at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session


class Permission:
    READ = "read"                    # no state change
    MUTATE_CART = "mutate_cart"      # changes a cart, never money
    PROPOSE = "propose"              # produces a proposal for a human to approve


@dataclass
class ToolSpec:
    name: str
    description: str
    permission: str
    parameters: dict[str, str] = field(default_factory=dict)
    handler: Callable[..., Any] | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description,
                "permission": self.permission, "parameters": self.parameters}


#: Capabilities that are explicitly *not* exposed to the model, and why.
FORBIDDEN_CAPABILITIES: list[dict[str, str]] = [
    {"name": "create_payment",
     "reason": "Payments are only created by the checkout service after an explicit "
               "human confirmation of a specific quote."},
    {"name": "capture_payment",
     "reason": "Capture is driven by server-side verification with the provider."},
    {"name": "refund_payment",
     "reason": "Refunds are a merchant action performed from the console."},
    {"name": "verify_payment",
     "reason": "Verification is a server-to-provider call; its result is not "
               "influenceable by model output."},
    {"name": "set_price",
     "reason": "Prices come from the catalog. The model has no write access to them."},
    {"name": "apply_discount",
     "reason": "Discounts come from merchant-approved campaigns and are capped by "
               "MAX_DISCOUNT_PERCENT."},
    {"name": "activate_campaign",
     "reason": "An AI-proposed campaign is created as PENDING_APPROVAL and needs "
               "merchant activation."},
    {"name": "update_policy",
     "reason": "Purchase policy is merchant- and environment-controlled."},
    {"name": "update_inventory",
     "reason": "Stock changes only as a consequence of a verified payment."},
    {"name": "confirm_payment_on_behalf_of_user",
     "reason": "Confirmation is a human act. An agent asserting the user 'probably "
               "wants this' is never accepted as authorization."},
]


def build_registry() -> dict[str, ToolSpec]:
    """The complete set of tools available to an agent turn."""
    from ..services import cart as cart_service
    from ..services import catalog as catalog_service
    from ..services import checkout as checkout_service
    from ..services import recommend as recommend_service
    from ..services import upsell as upsell_service
    from ..services.merchant import get_merchant

    def search_catalog(db: Session, query: str, **kwargs) -> list[dict]:
        results = catalog_service.search_products(db, query, limit=kwargs.get("limit", 10))
        return [catalog_service.product_to_dict(p, include_relations=False)
                for p, _ in results]

    def get_product(db: Session, product_id: str) -> dict | None:
        product = catalog_service.get_product(db, product_id)
        return catalog_service.product_to_dict(product) if product else None

    def check_inventory(db: Session, product_id: str) -> dict:
        product = catalog_service.get_product(db, product_id)
        if product is None:
            return {"product_id": product_id, "exists": False}
        return {"product_id": product_id, "exists": True, "inventory": product.inventory,
                "in_stock": product.inventory > 0, "active": product.active}

    def calculate_cart(db: Session, session_id: str) -> dict:
        order = cart_service.get_active_cart(db, session_id)
        if order is None:
            return cart_service.empty_cart_view(session_id)
        return cart_service.cart_view(db, order)

    def recommend_products(db: Session, query: str, session_id: str | None = None) -> list[dict]:
        req = recommend_service.extract_requirements(
            query, known_brands=catalog_service.brands(db))
        cart_ids = []
        if session_id:
            order = cart_service.get_active_cart(db, session_id)
            cart_ids = [i.product_id for i in order.items] if order else []
        return [s.to_dict() for s in
                recommend_service.rank_candidates(db, req, cart_product_ids=cart_ids)]

    def add_to_cart(db: Session, session_id: str, product_id: str,
                    quantity: int = 1, source: str = "direct") -> dict:
        order = cart_service.add_item(db, session_id, product_id, quantity,
                                      source=source, actor="ai_agent",
                                      actor_type="ai_agent")
        return cart_service.cart_view(db, order)

    def remove_from_cart(db: Session, session_id: str, product_id: str) -> dict:
        order = cart_service.remove_item(db, session_id, product_id,
                                         actor="ai_agent", actor_type="ai_agent")
        return cart_service.cart_view(db, order)

    def suggest_upsells(db: Session, session_id: str) -> dict:
        order = cart_service.get_active_cart(db, session_id)
        if order is None or not order.items:
            return {"suggestions": [], "rejected": [], "base_total_paise": 0, "bounds": {}}
        merchant = get_merchant(db)
        priced = cart_service.recalculate(db, order)
        return upsell_service.suggest(
            db,
            [{"product_id": i.product_id, "name": i.name, "quantity": i.quantity}
             for i in order.items],
            subtotal_paise=priced.subtotal_paise,
            max_order_value_paise=merchant.max_order_value_paise,
            upsell_enabled=merchant.upsell_enabled,
            cross_sell_enabled=merchant.cross_sell_enabled,
            tax_percent=merchant.tax_percent,
        ).to_dict()

    def prepare_checkout(db: Session, session_id: str) -> dict:
        # Produces a quote for a human to approve. Creates no payment.
        return checkout_service.prepare_checkout(
            db, session_id, actor="ai_agent", actor_type="ai_agent")

    def request_payment_confirmation(db: Session, session_id: str) -> dict:
        result = checkout_service.prepare_checkout(
            db, session_id, actor="ai_agent", actor_type="ai_agent")
        result["agent_note"] = (
            "This is a request for the shopper to approve. The agent cannot confirm "
            "it, and no payment exists until a human confirms this exact quote."
        )
        return result

    specs = [
        ToolSpec("search_catalog", "Full-text search over the merchant's catalog.",
                 Permission.READ, {"query": "string", "limit": "int"}, search_catalog),
        ToolSpec("get_product", "Fetch one product's authoritative record.",
                 Permission.READ, {"product_id": "string"}, get_product),
        ToolSpec("check_inventory", "Current stock and active flag for a product.",
                 Permission.READ, {"product_id": "string"}, check_inventory),
        ToolSpec("calculate_cart", "Backend-computed cart totals. The only source of "
                                   "cart money figures.",
                 Permission.READ, {"session_id": "string"}, calculate_cart),
        ToolSpec("recommend_products", "Rank catalog products against stated needs.",
                 Permission.READ, {"query": "string", "session_id": "string"},
                 recommend_products),
        ToolSpec("suggest_upsells", "Bounded add-ons drawn from curated catalog "
                                    "relationships.",
                 Permission.READ, {"session_id": "string"}, suggest_upsells),
        ToolSpec("add_to_cart", "Add a catalog product to the shopper's cart. Changes "
                                "no money and charges nothing.",
                 Permission.MUTATE_CART,
                 {"session_id": "string", "product_id": "string", "quantity": "int",
                  "source": "direct|upsell|cross_sell"}, add_to_cart),
        ToolSpec("remove_from_cart", "Remove a product from the cart.",
                 Permission.MUTATE_CART, {"session_id": "string", "product_id": "string"},
                 remove_from_cart),
        ToolSpec("prepare_checkout", "Price and policy-check the cart, producing a "
                                     "quote for the shopper to review. Creates no payment.",
                 Permission.PROPOSE, {"session_id": "string"}, prepare_checkout),
        ToolSpec("request_payment_confirmation",
                 "Ask the shopper to approve a specific total. The agent cannot "
                 "approve on their behalf.",
                 Permission.PROPOSE, {"session_id": "string"}, request_payment_confirmation),
    ]
    return {spec.name: spec for spec in specs}


_REGISTRY: dict[str, ToolSpec] | None = None


def registry() -> dict[str, ToolSpec]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_registry()
    return _REGISTRY


class ToolPermissionError(Exception):
    """An attempt to invoke something outside the registry."""


def call_tool(db: Session, name: str, **kwargs) -> Any:
    """Invoke a registered tool. Anything unregistered raises."""
    spec = registry().get(name)
    if spec is None or spec.handler is None:
        forbidden = next((f for f in FORBIDDEN_CAPABILITIES if f["name"] == name), None)
        if forbidden:
            raise ToolPermissionError(
                f"'{name}' is not available to the AI agent. {forbidden['reason']}"
            )
        raise ToolPermissionError(f"'{name}' is not a registered agent tool.")
    return spec.handler(db, **kwargs)


def describe() -> dict:
    """Rendered by the merchant console's AI Agent panel."""
    specs = registry()
    return {
        "allowed_tools": [s.to_dict() for s in specs.values()],
        "forbidden_capabilities": FORBIDDEN_CAPABILITIES,
        "permissions": {
            Permission.READ: "Reads authoritative data. No state change.",
            Permission.MUTATE_CART: "Can change cart contents. Cannot change prices, "
                                    "discounts, policy or payment state.",
            Permission.PROPOSE: "Can produce a priced, policy-checked proposal that a "
                                "human must approve.",
        },
        "money_boundary": (
            "No tool in this registry can create, capture, verify or refund a payment. "
            "Money movement requires an explicit human confirmation of a specific "
            "quote, submitted to the checkout service, which then calls the payment "
            "provider directly."
        ),
    }


__all__ = ["Permission", "ToolSpec", "registry", "call_tool", "describe",
           "ToolPermissionError", "FORBIDDEN_CAPABILITIES", "build_registry"]
