"""Payment endpoints — the human side of the money action gate.

`/confirm` is the only route in the application that can cause a payment to be
created, and it requires `confirmed: true` from a human. `/verify` is the only
route that can move an order to PAID, and it does so purely on the outcome of a
server-to-provider check; the request body is treated as an untrusted claim.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain.idempotency import IdempotencyConflict, IdempotencyInProgress
from ..domain.money import format_inr
from ..models import CheckoutQuote, Order, Transaction
from ..payments import PaymentProviderError, get_payment_provider
from ..schemas import (ConfirmPaymentRequest, PaymentFailureRequest,
                       PrepareCheckoutRequest, SandboxPayRequest,
                       VerifyPaymentRequest)
from ..services import checkout as checkout_service
from .deps import http_from_checkout_error, request_id

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.get("/config", summary="Public payment configuration")
def payment_config():
    """Only non-secret values. The Razorpay key *secret* never appears here."""
    provider = get_payment_provider()
    config = provider.public_config()
    config["banner"] = (
        "RAZORPAY TEST MODE — no real money moves."
        if provider.name == "razorpay_test" else
        "LOCAL SANDBOX — payments are simulated locally, this is NOT Razorpay."
    )
    return config


@router.get("/health", summary="Payment provider reachability")
def payment_health():
    return get_payment_provider().health()


@router.post("/prepare", summary="Step 1 — price, policy-check and explain")
def prepare(payload: PrepareCheckoutRequest, request: Request,
            db: Session = Depends(get_db)):
    try:
        result = checkout_service.prepare_checkout(
            db, payload.session_id, actor="buyer", actor_type="human",
            request_id=request_id(request))
    except checkout_service.CheckoutError as exc:
        db.commit()
        raise http_from_checkout_error(exc) from exc
    db.commit()
    return result


@router.post("/confirm", summary="Step 2 — explicit human confirmation creates the order")
def confirm(payload: ConfirmPaymentRequest, request: Request,
            db: Session = Depends(get_db)):
    """Requires `confirmed: true`. Idempotent on the quote id."""
    if not payload.confirmed:
        raise HTTPException(400, detail={
            "error": "confirmation_required",
            "message": ("Payment requires explicit confirmation. Send confirmed: true "
                        "only when the person paying has approved the exact total."),
        })
    try:
        return checkout_service.confirm_and_create_payment(
            db, payload.quote_id, confirmed=True, confirmed_by=payload.confirmed_by,
            idempotency_key=payload.idempotency_key, actor_type="human",
            request_id=request_id(request))
    except IdempotencyConflict as exc:
        raise HTTPException(409, detail={"error": "idempotency_conflict",
                                         "message": str(exc)}) from exc
    except IdempotencyInProgress as exc:
        raise HTTPException(409, detail={"error": "request_in_progress",
                                         "message": str(exc)}) from exc
    except checkout_service.CheckoutError as exc:
        raise http_from_checkout_error(exc) from exc


@router.post("/verify", summary="Step 3 — server-side verification (the only path to PAID)")
def verify(payload: VerifyPaymentRequest, request: Request,
           db: Session = Depends(get_db)):
    """The body is a *claim* from the browser. It is verified, never believed."""
    try:
        return checkout_service.verify_payment(
            db,
            provider_order_id=payload.razorpay_order_id,
            provider_payment_id=payload.razorpay_payment_id,
            signature=payload.razorpay_signature,
            actor="buyer", actor_type="human", request_id=request_id(request))
    except checkout_service.CheckoutError as exc:
        raise http_from_checkout_error(exc) from exc


@router.post("/failed", summary="Report a failed or dismissed payment")
def failed(payload: PaymentFailureRequest, request: Request,
           db: Session = Depends(get_db)):
    try:
        return checkout_service.report_payment_failure(
            db, provider_order_id=payload.razorpay_order_id, reason=payload.reason,
            actor="buyer", actor_type="human", request_id=request_id(request))
    except checkout_service.CheckoutError as exc:
        raise http_from_checkout_error(exc) from exc


@router.post("/reconcile/{order_id}", summary="Recovery — ask the provider what happened")
def reconcile(order_id: str, request: Request, db: Session = Depends(get_db)):
    try:
        return checkout_service.reconcile_order(
            db, order_id, actor="merchant", actor_type="merchant",
            request_id=request_id(request))
    except checkout_service.CheckoutError as exc:
        raise http_from_checkout_error(exc) from exc


@router.post("/cancel/{order_id}", summary="Cancel an unpaid order")
def cancel(order_id: str, db: Session = Depends(get_db)):
    try:
        return checkout_service.cancel_order(db, order_id, reason="Cancelled by buyer.")
    except checkout_service.CheckoutError as exc:
        raise http_from_checkout_error(exc) from exc


@router.post("/sandbox/pay", summary="Complete a payment in the offline sandbox")
def sandbox_pay(payload: SandboxPayRequest, db: Session = Depends(get_db)):
    """Stands in for the hosted checkout when no Razorpay keys are configured.

    Returns the same (order id, payment id, signature) triple Razorpay Checkout
    hands to the browser, which is then submitted to `/verify` exactly as a real
    payment would be. Not available when a real Razorpay provider is active.
    """
    provider = get_payment_provider()
    if provider.name != "local_sandbox":
        raise HTTPException(400, detail={
            "error": "sandbox_unavailable",
            "message": (f"The active provider is {provider.name}. Complete the payment "
                        "through the provider's own checkout instead."),
        })
    try:
        result = provider.attempt_payment(payload.provider_order_id, payload.outcome,
                                          payload.amount_paise_override)
    except PaymentProviderError as exc:
        raise HTTPException(502, detail={"error": exc.code, "message": str(exc)}) from exc
    result["next_step"] = ("POST this to /api/payments/verify — the server will "
                           "independently verify it before any order is marked paid.")
    return result


@router.get("/quote/{quote_id}", summary="Fetch a checkout quote")
def get_quote(quote_id: str, db: Session = Depends(get_db)):
    quote = db.get(CheckoutQuote, quote_id)
    if quote is None:
        raise HTTPException(404, detail={"error": "quote_not_found",
                                         "message": f"No quote {quote_id}."})
    return {
        "quote_id": quote.id, "order_id": quote.order_id,
        "total_paise": quote.total_paise, "total_display": format_inr(quote.total_paise),
        "currency": quote.currency, "breakdown": quote.breakdown,
        "policy_result": quote.policy_result, "explanation": quote.explanation,
        "expires_at": quote.expires_at.isoformat(),
        "confirmed_at": quote.confirmed_at.isoformat() if quote.confirmed_at else None,
        "confirmed_by": quote.confirmed_by, "consumed": quote.consumed,
        "cart_fingerprint": quote.cart_fingerprint,
    }


@router.get("/order/{order_id}", summary="Order with its payment history")
def order_detail(order_id: str, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, detail={"error": "order_not_found",
                                         "message": f"No order {order_id}."})
    transactions = db.scalars(
        select(Transaction).where(Transaction.order_id == order_id)
        .order_by(Transaction.created_at)).all()
    return {
        "order_id": order.id, "session_id": order.session_id, "status": order.status,
        "paid": order.status == "PAID",
        "items": [{"product_id": i.product_id, "name": i.name, "sku": i.sku,
                   "quantity": i.quantity, "unit_price_paise": i.unit_price_paise,
                   "unit_price_display": format_inr(i.unit_price_paise),
                   "line_total_paise": i.line_total_paise,
                   "line_total_display": format_inr(i.line_total_paise),
                   "source": i.source} for i in order.items],
        "subtotal_paise": order.subtotal_paise,
        "subtotal_display": format_inr(order.subtotal_paise),
        "discount_paise": order.discount_paise,
        "discount_display": format_inr(order.discount_paise),
        "tax_paise": order.tax_paise, "tax_display": format_inr(order.tax_paise),
        "total_paise": order.total_paise, "total_display": format_inr(order.total_paise),
        "currency": order.currency,
        "payment_provider": order.payment_provider,
        "payment_order_id": order.payment_order_id, "payment_id": order.payment_id,
        "is_synthetic": order.is_synthetic,
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat(),
        "transactions": [{
            "transaction_id": t.id, "provider": t.provider,
            "provider_order_id": t.provider_order_id,
            "provider_payment_id": t.provider_payment_id,
            "amount_paise": t.amount_paise, "amount_display": format_inr(t.amount_paise),
            "currency": t.currency, "status": t.status,
            "verification_status": t.verification_status,
            "signature_present": t.provider_signature_present,
            "failure_reason": t.failure_reason, "is_test_mode": t.is_test_mode,
            "provider_meta": t.provider_meta,
            "created_at": t.created_at.isoformat(),
            "updated_at": t.updated_at.isoformat(),
        } for t in transactions],
    }


__all__ = ["router"]
