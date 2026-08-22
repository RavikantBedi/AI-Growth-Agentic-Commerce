"""Order and payment state machines.

Transitions are validated centrally. Nothing in the API layer is allowed to
write a status field directly — every change goes through `transition_order`
or `transition_payment`, which raise on an illegal move. This is what stops a
crafted request from pushing an order to PAID.
"""
from __future__ import annotations

from enum import Enum


class OrderStatus(str, Enum):
    DRAFT = "DRAFT"
    CART = "CART"
    CHECKOUT_PENDING = "CHECKOUT_PENDING"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAID = "PAID"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class PaymentStatus(str, Enum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    UNKNOWN = "UNKNOWN"


class VerificationStatus(str, Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNVERIFIABLE = "UNVERIFIABLE"


#: Only these order transitions exist. Anything else is a bug or an attack.
ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.DRAFT: {OrderStatus.CART, OrderStatus.CANCELLED},
    OrderStatus.CART: {OrderStatus.CHECKOUT_PENDING, OrderStatus.CANCELLED, OrderStatus.CART},
    OrderStatus.CHECKOUT_PENDING: {
        OrderStatus.PAYMENT_PENDING,
        OrderStatus.CART,          # buyer went back and edited the cart
        OrderStatus.CANCELLED,
    },
    OrderStatus.PAYMENT_PENDING: {
        OrderStatus.PAID,
        OrderStatus.PAYMENT_FAILED,
        OrderStatus.CANCELLED,
    },
    # A failed payment is retryable: back to checkout, never straight to PAID.
    OrderStatus.PAYMENT_FAILED: {
        OrderStatus.CHECKOUT_PENDING,
        OrderStatus.PAYMENT_PENDING,
        OrderStatus.CART,
        OrderStatus.CANCELLED,
    },
    OrderStatus.PAID: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}

PAYMENT_TRANSITIONS: dict[PaymentStatus, set[PaymentStatus]] = {
    PaymentStatus.CREATED: {
        PaymentStatus.PENDING,
        PaymentStatus.AUTHORIZED,
        PaymentStatus.CAPTURED,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.VERIFICATION_FAILED,
        PaymentStatus.UNKNOWN,
    },
    PaymentStatus.PENDING: {
        PaymentStatus.AUTHORIZED,
        PaymentStatus.CAPTURED,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.VERIFICATION_FAILED,
        PaymentStatus.UNKNOWN,
    },
    PaymentStatus.AUTHORIZED: {
        PaymentStatus.CAPTURED,
        PaymentStatus.FAILED,
        PaymentStatus.REFUNDED,
        PaymentStatus.VERIFICATION_FAILED,
    },
    PaymentStatus.CAPTURED: {PaymentStatus.REFUNDED},
    PaymentStatus.FAILED: {PaymentStatus.PENDING, PaymentStatus.CANCELLED},
    PaymentStatus.VERIFICATION_FAILED: {
        PaymentStatus.CAPTURED,     # a later re-verification succeeded
        PaymentStatus.AUTHORIZED,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
    },
    PaymentStatus.UNKNOWN: {
        PaymentStatus.AUTHORIZED,
        PaymentStatus.CAPTURED,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.VERIFICATION_FAILED,
    },
    PaymentStatus.CANCELLED: set(),
    PaymentStatus.REFUNDED: set(),
}

#: Payment states that are allowed to move an order to PAID. Nothing else may.
TERMINAL_SUCCESS_PAYMENT_STATES = {PaymentStatus.CAPTURED}


class IllegalTransition(Exception):
    """Raised when code (or a request) attempts an undefined state change."""

    def __init__(self, kind: str, current: str, target: str):
        self.kind, self.current, self.target = kind, current, target
        table = ORDER_TRANSITIONS if kind == "order" else PAYMENT_TRANSITIONS
        enum_cls = OrderStatus if kind == "order" else PaymentStatus
        allowed = sorted(s.value for s in table.get(enum_cls(current), set()))
        super().__init__(
            f"Illegal {kind} transition: {current} -> {target}. "
            f"Allowed from {current}: {allowed or 'none (terminal state)'}"
        )


def can_transition_order(current: OrderStatus | str, target: OrderStatus | str) -> bool:
    cur, tgt = OrderStatus(current), OrderStatus(target)
    return tgt in ORDER_TRANSITIONS.get(cur, set())


def can_transition_payment(current: PaymentStatus | str, target: PaymentStatus | str) -> bool:
    cur, tgt = PaymentStatus(current), PaymentStatus(target)
    return tgt in PAYMENT_TRANSITIONS.get(cur, set())


def transition_order(current: OrderStatus | str, target: OrderStatus | str) -> OrderStatus:
    """Return the new order status, or raise `IllegalTransition`."""
    cur, tgt = OrderStatus(current), OrderStatus(target)
    if cur == tgt and tgt not in ORDER_TRANSITIONS.get(cur, set()):
        return cur  # idempotent no-op
    if not can_transition_order(cur, tgt):
        raise IllegalTransition("order", cur.value, tgt.value)
    return tgt


def transition_payment(current: PaymentStatus | str, target: PaymentStatus | str) -> PaymentStatus:
    """Return the new payment status, or raise `IllegalTransition`."""
    cur, tgt = PaymentStatus(current), PaymentStatus(target)
    if cur == tgt:
        return cur  # idempotent no-op — provider polled twice, same answer
    if not can_transition_payment(cur, tgt):
        raise IllegalTransition("payment", cur.value, tgt.value)
    return tgt


__all__ = [
    "OrderStatus", "PaymentStatus", "VerificationStatus",
    "ORDER_TRANSITIONS", "PAYMENT_TRANSITIONS", "TERMINAL_SUCCESS_PAYMENT_STATES",
    "IllegalTransition", "can_transition_order", "can_transition_payment",
    "transition_order", "transition_payment",
]
