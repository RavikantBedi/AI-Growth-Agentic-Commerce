"""Shared API helpers."""
from __future__ import annotations

from fastapi import HTTPException, Request

from ..domain.states import IllegalTransition
from ..services.cart import CartError
from ..services.checkout import CheckoutError


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def http_from_cart_error(exc: CartError) -> HTTPException:
    return HTTPException(status_code=400, detail={
        "error": exc.code, "message": exc.message, **({"detail": exc.detail} if exc.detail else {})
    })


def http_from_checkout_error(exc: CheckoutError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail={
        "error": exc.code, "message": exc.message,
        **({"detail": exc.detail} if exc.detail else {}),
    })


def http_from_illegal_transition(exc: IllegalTransition) -> HTTPException:
    """A rejected state change is a client-visible conflict, never a 500."""
    return HTTPException(status_code=409, detail={
        "error": "illegal_state_transition",
        "message": (f"This {exc.kind} is {exc.current} and cannot move to "
                    f"{exc.target}. Nothing was changed and nothing was charged."),
        "detail": {"kind": exc.kind, "current": exc.current, "target": exc.target},
    })


__all__ = ["request_id", "http_from_cart_error", "http_from_checkout_error",
           "http_from_illegal_transition"]
