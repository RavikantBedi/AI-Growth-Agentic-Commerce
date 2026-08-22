"""The money action gate.

Every rupee that moves passes through this module, in this order:

    ACTION REQUEST -> VALIDATE INTENT -> VALIDATE CART -> VALIDATE PRICE
    -> VALIDATE INVENTORY -> VALIDATE POLICY -> EXPLANATION
    -> EXPLICIT USER CONFIRMATION -> CREATE PAYMENT ORDER -> PAYMENT
    -> SERVER VERIFICATION -> FINALIZE ORDER -> AUDIT

Three properties are structural, not conventional:

* The AI has no function here. `prepare_checkout` is reachable from the agent,
  but it only produces a *quote* — a priced, policy-checked proposal. Creating
  a payment requires `confirm_and_create_payment`, which demands an explicit
  human confirmation flag and a quote the human was shown.
* A quote stores a fingerprint of exactly what was approved. If the cart, a
  price, the tax or a discount changes afterwards, the fingerprint no longer
  matches and the payment is refused rather than re-priced.
* Only `verify_payment` — a server-side call to the provider — can move an
  order to PAID. No frontend callback, URL parameter or AI claim can.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..config import settings
from ..domain import audit
from ..domain.idempotency import idempotent
from ..domain.money import format_inr
from ..domain.policy import CartLineView, PolicyResult, evaluate
from ..domain.states import (IllegalTransition, OrderStatus, PaymentStatus,
                             VerificationStatus, transition_order,
                             transition_payment)
from ..models import CheckoutQuote, IdempotencyRecord, Order, Transaction
from ..payments import (PaymentProviderError, PaymentProviderUnavailable,
                        get_payment_provider)
from . import campaigns as campaign_service
from . import cart as cart_service
from . import catalog
from .merchant import build_policy, get_merchant

log = logging.getLogger("services.checkout")


class CheckoutError(Exception):
    def __init__(self, message: str, *, code: str = "checkout_error",
                 detail: dict | None = None, http_status: int = 400):
        super().__init__(message)
        self.message, self.code, self.detail = message, code, detail or {}
        self.http_status = http_status


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Step 1 — prepare: price, check, explain. No money moves here.
# ---------------------------------------------------------------------------
def prepare_checkout(db: Session, session_id: str, *, actor: str = "buyer",
                     actor_type: str = "human", request_id: str | None = None) -> dict:
    """Validate and price a cart, then produce a quote for human approval."""
    order = cart_service.get_active_cart(db, session_id)
    if order is None or not order.items:
        raise CheckoutError("Your cart is empty — there is nothing to check out.",
                            code="empty_cart")
    if order.status == OrderStatus.PAID.value:
        raise CheckoutError("This order has already been paid.", code="already_paid")

    merchant = get_merchant(db)
    policy = build_policy(merchant)

    priced = cart_service.recalculate(db, order, record_audit=True)

    products = catalog.get_products_by_ids(db, [i.product_id for i in order.items])
    lines: list[CartLineView] = []
    for item in order.items:
        product = products.get(item.product_id)
        lines.append(CartLineView(
            product_id=item.product_id,
            name=item.name,
            category=product.category if product else "",
            unit_price_paise=item.unit_price_paise,
            quantity=item.quantity,
            inventory_available=product.inventory if product else 0,
            active=bool(product and product.active),
            catalog_price_paise=product.price_paise if product else item.unit_price_paise,
        ))

    audit.record(
        db, audit.Action.INVENTORY_CHECKED, session_id=session_id, order_id=order.id,
        actor=actor, actor_type=actor_type,
        reason="Stock checked for every line immediately before quoting.",
        input_data={"lines": [{"product_id": l.product_id, "requested": l.quantity,
                               "available": l.inventory_available} for l in lines]},
        decision=audit.Decision.INFO, is_synthetic=order.is_synthetic,
    )

    result: PolicyResult = evaluate(
        policy, lines,
        total_paise=priced.total_paise,
        currency=priced.currency,
        discount_percent=priced.discount_percent,
    )

    audit.record(
        db, audit.Action.POLICY_CHECKED, session_id=session_id, order_id=order.id,
        actor=actor, actor_type=actor_type,
        reason=result.summary,
        decision=audit.Decision.ALLOWED if result.allowed else audit.Decision.REJECTED,
        policy_result=result.to_dict(), amount_paise=priced.total_paise,
        request_id=request_id, is_synthetic=order.is_synthetic,
    )

    if not result.allowed:
        raise CheckoutError(
            "This order cannot proceed: " + result.summary,
            code="policy_violation",
            detail={"policy_result": result.to_dict(), "cart": priced.to_dict()},
        )

    try:
        order.status = transition_order(order.status, OrderStatus.CHECKOUT_PENDING).value
    except IllegalTransition as exc:
        raise CheckoutError(str(exc), code="illegal_transition") from exc

    explanation = build_explanation(priced, result, merchant.name)

    quote = CheckoutQuote(
        order_id=order.id, session_id=session_id,
        subtotal_paise=priced.subtotal_paise, discount_paise=priced.discount_paise,
        tax_paise=priced.tax_paise, shipping_paise=priced.shipping_paise,
        total_paise=priced.total_paise, currency=priced.currency,
        cart_fingerprint=priced.fingerprint(),
        breakdown=priced.to_dict(), policy_result=result.to_dict(),
        explanation=explanation,
        expires_at=_now() + timedelta(seconds=settings.quote_ttl_seconds),
    )
    db.add(quote)
    db.flush()

    audit.record(
        db, audit.Action.PAYMENT_CONFIRMATION_REQUESTED, session_id=session_id,
        order_id=order.id, actor=actor, actor_type=actor_type,
        reason=(f"Quote {quote.id} presented for explicit approval: "
                f"{format_inr(priced.total_paise)} for {priced.item_count} item(s)."),
        input_data={"quote_id": quote.id, "fingerprint": quote.cart_fingerprint},
        decision=audit.Decision.INFO, amount_paise=priced.total_paise,
        policy_result=result.to_dict(), request_id=request_id,
        is_synthetic=order.is_synthetic,
    )

    provider = get_payment_provider()
    return {
        "quote_id": quote.id,
        "order_id": order.id,
        "session_id": session_id,
        "status": order.status,
        "cart": priced.to_dict(),
        "policy_result": result.to_dict(),
        "explanation": explanation,
        "requires_confirmation": True,
        "expires_at": quote.expires_at.isoformat(),
        "payment_provider": provider.public_config(),
        "test_mode": True,
        "notice": ("No payment has been created yet. A charge is only prepared after "
                   "you explicitly confirm this exact amount."),
    }


def build_explanation(priced, policy_result: PolicyResult, merchant_name: str) -> str:
    """The human-readable 'here is exactly what you will be charged, and why'."""
    lines = [f"You are about to pay {format_inr(priced.total_paise)} to {merchant_name}.", ""]
    for line in priced.lines:
        tag = {"upsell": " [suggested add-on]",
               "cross_sell": " [suggested add-on]"}.get(line.source, "")
        lines.append(
            f"  {line.name} × {line.quantity} @ {format_inr(line.unit_price_paise)}"
            f" = {format_inr(line.line_total_paise)}{tag}"
        )
    lines.append("")
    lines.append(f"  Subtotal                {format_inr(priced.subtotal_paise)}")
    if priced.discount_paise:
        label = priced.discount_label or "Discount"
        lines.append(f"  Discount ({label})   -{format_inr(priced.discount_paise)}")
    if priced.tax_paise:
        lines.append(f"  GST @ {priced.tax_percent:g}%             "
                     f"{format_inr(priced.tax_paise)}")
    if priced.shipping_paise:
        lines.append(f"  Shipping                {format_inr(priced.shipping_paise)}")
    lines.append(f"  TOTAL ({priced.currency})           {format_inr(priced.total_paise)}")
    lines.append("")
    lines.append("Why this total: every price is read from the live catalog at checkout "
                 "time; tax and any discount are computed on the server.")
    lines.append(f"Policy: {policy_result.summary}")
    lines.append("This is a TEST-MODE payment. No real money will move.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 2 — explicit confirmation, then create the provider order
# ---------------------------------------------------------------------------
def confirm_and_create_payment(
    db: Session,
    quote_id: str,
    *,
    confirmed: bool,
    confirmed_by: str = "buyer",
    idempotency_key: str | None = None,
    actor_type: str = "human",
    request_id: str | None = None,
) -> dict:
    """Turn an approved quote into a real provider payment order.

    `confirmed` must be explicitly True. An AI agent cannot supply it on the
    user's behalf: the API layer only accepts it from the human confirmation
    endpoint, and it is recorded verbatim in the audit trail.
    """
    quote = db.get(CheckoutQuote, quote_id)
    if quote is None:
        raise CheckoutError("Unknown checkout quote.", code="quote_not_found", http_status=404)

    order = db.get(Order, quote.order_id)
    if order is None:
        raise CheckoutError("The order for this quote no longer exists.",
                            code="order_not_found", http_status=404)

    # A quote is single-use and an order is payable exactly once. Refuse
    # clearly here rather than letting the state machine raise deeper in.
    if order.status == OrderStatus.PAID.value:
        raise CheckoutError(
            f"Order {order.id} has already been paid. No further payment can be "
            "created for it.",
            code="already_paid", http_status=409,
            detail={"order_id": order.id, "status": order.status},
        )
    if order.status in (OrderStatus.CANCELLED.value, OrderStatus.REFUNDED.value):
        raise CheckoutError(
            f"Order {order.id} is {order.status} and can no longer be paid.",
            code="order_not_payable", http_status=409,
            detail={"order_id": order.id, "status": order.status},
        )
    idem_key = idempotency_key or f"quote:{quote.id}"
    if order.status == OrderStatus.PAYMENT_PENDING.value and quote.consumed:
        # A retry under a key we have already served replays that response.
        # A brand-new key here would mean a *second* payment order for a cart
        # that already has one, so it is refused.
        known = db.get(IdempotencyRecord, f"create_payment_order:{idem_key}")
        if known is None:
            raise CheckoutError(
                f"A payment is already in progress for order {order.id} "
                f"({order.payment_order_id}). Complete or cancel it before "
                "starting another.",
                code="payment_already_in_progress", http_status=409,
                detail={"order_id": order.id,
                        "payment_order_id": order.payment_order_id},
            )

    merchant = get_merchant(db)
    policy = build_policy(merchant)

    if policy.requires_confirmation and not confirmed:
        audit.record(
            db, audit.Action.PAYMENT_CONFIRMED_BY_USER, session_id=quote.session_id,
            order_id=order.id, actor=confirmed_by, actor_type=actor_type,
            reason="Payment attempted without explicit user confirmation. Refused.",
            decision=audit.Decision.REJECTED, amount_paise=quote.total_paise,
            request_id=request_id, is_synthetic=order.is_synthetic,
        )
        db.commit()
        raise CheckoutError(
            "Explicit confirmation is required before a payment can be created.",
            code="confirmation_required",
        )

    if quote.expires_at < _now():
        raise CheckoutError(
            "This quote has expired. Prices and stock are re-checked for every "
            "payment, so please review the order again.",
            code="quote_expired",
        )

    # The cart must still be exactly what the human approved.
    priced = cart_service.recalculate(db, order)
    if priced.fingerprint() != quote.cart_fingerprint:
        audit.record(
            db, audit.Action.POLICY_CHECKED, session_id=quote.session_id, order_id=order.id,
            actor=confirmed_by, actor_type=actor_type,
            reason=("The cart changed after the quote was approved. Refusing to charge "
                    "an amount the buyer did not see."),
            decision=audit.Decision.REJECTED,
            input_data={"approved_total_paise": quote.total_paise,
                        "current_total_paise": priced.total_paise},
            amount_paise=priced.total_paise, request_id=request_id,
            is_synthetic=order.is_synthetic,
        )
        db.commit()
        raise CheckoutError(
            f"The cart changed after you approved {format_inr(quote.total_paise)} "
            f"(it is now {format_inr(priced.total_paise)}). Review and approve the "
            "new total before paying.",
            code="quote_stale",
            detail={"approved_total_paise": quote.total_paise,
                    "current_total_paise": priced.total_paise},
        )

    # Re-run the full policy check against live data. Stock may have gone.
    products = catalog.get_products_by_ids(db, [i.product_id for i in order.items])
    lines = [
        CartLineView(
            product_id=i.product_id, name=i.name,
            category=products[i.product_id].category if i.product_id in products else "",
            unit_price_paise=i.unit_price_paise, quantity=i.quantity,
            inventory_available=products[i.product_id].inventory if i.product_id in products else 0,
            active=bool(i.product_id in products and products[i.product_id].active),
            catalog_price_paise=(products[i.product_id].price_paise
                                 if i.product_id in products else i.unit_price_paise),
        )
        for i in order.items
    ]
    result = evaluate(policy, lines, total_paise=priced.total_paise,
                      currency=priced.currency, discount_percent=priced.discount_percent)

    audit.record(
        db, audit.Action.POLICY_CHECKED, session_id=quote.session_id, order_id=order.id,
        actor=confirmed_by, actor_type=actor_type,
        reason="Re-checked policy against live catalog data at payment time. " + result.summary,
        decision=audit.Decision.ALLOWED if result.allowed else audit.Decision.REJECTED,
        policy_result=result.to_dict(), amount_paise=priced.total_paise,
        request_id=request_id, is_synthetic=order.is_synthetic,
    )
    if not result.allowed:
        db.commit()
        raise CheckoutError("This order can no longer proceed: " + result.summary,
                            code="policy_violation",
                            detail={"policy_result": result.to_dict()})

    audit.record(
        db, audit.Action.PAYMENT_CONFIRMED_BY_USER, session_id=quote.session_id,
        order_id=order.id, actor=confirmed_by, actor_type=actor_type,
        reason=(f"{confirmed_by} explicitly confirmed payment of "
                f"{format_inr(quote.total_paise)} against quote {quote.id}."),
        input_data={"quote_id": quote.id, "confirmed": True,
                    "fingerprint": quote.cart_fingerprint},
        decision=audit.Decision.ALLOWED, amount_paise=quote.total_paise,
        request_id=request_id, is_synthetic=order.is_synthetic,
    )

    def _create() -> dict:
        return _create_provider_order(db, order, quote, confirmed_by, actor_type, request_id)

    response, replayed = idempotent(
        db, idem_key, "create_payment_order",
        {"quote_id": quote.id, "total_paise": quote.total_paise,
         "fingerprint": quote.cart_fingerprint},
        _create,
    )
    if replayed:
        audit.record(
            db, audit.Action.IDEMPOTENT_REPLAY, session_id=quote.session_id,
            order_id=order.id, actor=confirmed_by, actor_type=actor_type,
            reason=(f"Duplicate payment request for quote {quote.id} was replayed "
                    "from the idempotency record; no second payment order created."),
            input_data={"idempotency_key": idem_key},
            decision=audit.Decision.ALLOWED,
            amount_paise=quote.total_paise, request_id=request_id,
            is_synthetic=order.is_synthetic,
        )
    response["replayed"] = replayed
    db.commit()
    return response


def _create_provider_order(db: Session, order: Order, quote: CheckoutQuote,
                           confirmed_by: str, actor_type: str,
                           request_id: str | None) -> dict:
    provider = get_payment_provider()
    try:
        provider_order = provider.create_order(
            amount_paise=quote.total_paise,
            currency=quote.currency,
            receipt=order.id,
            notes={"order_id": order.id, "session_id": order.session_id,
                   "quote_id": quote.id, "mode": "test"},
        )
    except PaymentProviderUnavailable as exc:
        audit.record(
            db, audit.Action.PAYMENT_PROVIDER_UNAVAILABLE, session_id=order.session_id,
            order_id=order.id, actor=confirmed_by, actor_type=actor_type,
            reason=str(exc), decision=audit.Decision.REJECTED,
            amount_paise=quote.total_paise, status="PROVIDER_UNAVAILABLE",
            request_id=request_id, is_synthetic=order.is_synthetic,
        )
        db.commit()
        raise CheckoutError(
            f"The payment provider is unreachable, so no payment was created. "
            f"Your order is still {order.status} and nothing has been charged. "
            f"You can retry checkout. ({exc})",
            code="provider_unavailable", detail={"retryable": True}, http_status=503,
        ) from exc
    except PaymentProviderError as exc:
        audit.record(
            db, audit.Action.PAYMENT_ORDER_CREATED, session_id=order.session_id,
            order_id=order.id, actor=confirmed_by, actor_type=actor_type,
            reason=f"Provider rejected the order: {exc}", decision=audit.Decision.REJECTED,
            amount_paise=quote.total_paise, status="REJECTED", request_id=request_id,
            is_synthetic=order.is_synthetic,
        )
        db.commit()
        raise CheckoutError(f"The payment provider rejected this order: {exc}",
                            code="provider_rejected") from exc

    transaction = Transaction(
        order_id=order.id, provider=provider.name,
        provider_order_id=provider_order.id,
        amount_paise=quote.total_paise, currency=quote.currency,
        status=PaymentStatus.CREATED.value,
        verification_status=VerificationStatus.NOT_ATTEMPTED.value,
        is_test_mode=provider.is_test_mode,
        provider_meta=provider_order.raw,
    )
    db.add(transaction)

    order.payment_provider = provider.name
    order.payment_order_id = provider_order.id
    order.status = transition_order(order.status, OrderStatus.PAYMENT_PENDING).value

    quote.confirmed_at = _now()
    quote.confirmed_by = confirmed_by
    quote.consumed = True
    db.flush()

    audit.record(
        db, audit.Action.PAYMENT_ORDER_CREATED, session_id=order.session_id,
        order_id=order.id, actor=confirmed_by, actor_type=actor_type,
        reason=(f"{provider.display_label} order {provider_order.id} created for "
                f"{format_inr(quote.total_paise)} after explicit confirmation."),
        input_data={"quote_id": quote.id, "provider": provider.name},
        decision=audit.Decision.ALLOWED, payment_reference=provider_order.id,
        amount_paise=quote.total_paise, status=PaymentStatus.CREATED.value,
        request_id=request_id, is_synthetic=order.is_synthetic,
    )

    return {
        "order_id": order.id,
        "transaction_id": transaction.id,
        "provider": provider.name,
        "provider_order_id": provider_order.id,
        "amount_paise": quote.total_paise,
        "amount_display": format_inr(quote.total_paise),
        "currency": quote.currency,
        "status": order.status,
        "payment_status": PaymentStatus.CREATED.value,
        "test_mode": provider.is_test_mode,
        "provider_config": provider.public_config(),
        "notice": ("A TEST-MODE payment order has been created. The order will only "
                   "be marked paid after the server verifies the payment with the "
                   "provider."),
    }


# ---------------------------------------------------------------------------
# Step 3 — server-side verification. The only path to PAID.
# ---------------------------------------------------------------------------
def verify_payment(
    db: Session,
    *,
    provider_order_id: str,
    provider_payment_id: str,
    signature: str,
    actor: str = "buyer",
    actor_type: str = "human",
    request_id: str | None = None,
) -> dict:
    """Verify a payment with the provider and finalise the order accordingly."""
    order = db.query(Order).filter(Order.payment_order_id == provider_order_id).first()
    if order is None:
        raise CheckoutError("No order matches that payment order id.",
                            code="order_not_found", http_status=404)

    transaction = (db.query(Transaction)
                   .filter(Transaction.provider_order_id == provider_order_id)
                   .order_by(Transaction.created_at.desc()).first())
    if transaction is None:
        raise CheckoutError("No transaction recorded for that payment order.",
                            code="transaction_not_found", http_status=404)

    if order.status == OrderStatus.PAID.value:
        # Verification replayed after success — report the stored truth.
        return _payment_result(order, transaction,
                               "This order was already verified and marked paid.",
                               replayed=True)

    provider = get_payment_provider()
    audit.record(
        db, audit.Action.PAYMENT_ATTEMPTED, session_id=order.session_id, order_id=order.id,
        actor=actor, actor_type=actor_type,
        reason=(f"Payment callback received for {provider_order_id}; starting "
                "server-side verification. The client's claim is not trusted."),
        input_data={"provider_order_id": provider_order_id,
                    "provider_payment_id": provider_payment_id,
                    "signature_present": bool(signature)},
        decision=audit.Decision.INFO, payment_reference=provider_payment_id,
        amount_paise=transaction.amount_paise, request_id=request_id,
        is_synthetic=order.is_synthetic,
    )

    try:
        verification = provider.verify_payment(
            provider_order_id=provider_order_id,
            provider_payment_id=provider_payment_id,
            signature=signature,
            expected_amount_paise=transaction.amount_paise,
        )
    except PaymentProviderUnavailable as exc:
        transaction.status = transition_payment(transaction.status, PaymentStatus.UNKNOWN).value
        transaction.verification_status = VerificationStatus.UNVERIFIABLE.value
        transaction.failure_reason = str(exc)
        db.flush()
        audit.record(
            db, audit.Action.PAYMENT_PROVIDER_UNAVAILABLE, session_id=order.session_id,
            order_id=order.id, actor=actor, actor_type=actor_type,
            reason=(f"Verification could not be completed: {exc}. The order stays "
                    f"{order.status} and was NOT marked paid."),
            decision=audit.Decision.REJECTED, payment_reference=provider_payment_id,
            amount_paise=transaction.amount_paise, status=PaymentStatus.UNKNOWN.value,
            request_id=request_id, is_synthetic=order.is_synthetic,
        )
        db.commit()
        raise CheckoutError(
            "We could not reach the payment provider to verify this payment. Your "
            "order has NOT been marked paid. If money was debited it will be "
            "reconciled — use 'Re-check payment status' in a moment.",
            code="verification_unavailable",
            detail={"order_id": order.id, "retryable": True,
                    "recovery_action": f"/api/payments/reconcile/{order.id}"},
            http_status=503,
        ) from exc
    except PaymentProviderError as exc:
        verification = None
        log.warning("verification error for %s: %s", provider_order_id, exc)

    if verification is None or not verification.verified:
        return _finalize_failure(db, order, transaction, verification, actor, actor_type,
                                 request_id)

    return _finalize_success(db, order, transaction, verification, actor, actor_type,
                             request_id)


def _finalize_success(db: Session, order: Order, transaction: Transaction,
                      verification, actor: str, actor_type: str,
                      request_id: str | None) -> dict:
    payment = verification.payment
    transaction.status = transition_payment(transaction.status, verification.status).value
    transaction.verification_status = VerificationStatus.VERIFIED.value
    transaction.provider_payment_id = payment.id if payment else None
    transaction.provider_signature_present = verification.signature_valid
    transaction.provider_meta = {**(transaction.provider_meta or {}),
                                 **(payment.raw if payment else {})}
    transaction.failure_reason = None

    order.payment_id = transaction.provider_payment_id
    order.status = transition_order(order.status, OrderStatus.PAID).value
    db.flush()

    audit.record(
        db, audit.Action.PAYMENT_VERIFIED, session_id=order.session_id, order_id=order.id,
        actor="payment-service", actor_type="system", reason=verification.reason,
        decision=audit.Decision.ALLOWED, payment_reference=transaction.provider_payment_id,
        amount_paise=transaction.amount_paise, status=PaymentStatus.CAPTURED.value,
        policy_result=verification.to_dict(), request_id=request_id,
        is_synthetic=order.is_synthetic,
    )

    # Inventory moves only after a verified payment, never on a failure.
    decremented = _decrement_inventory(db, order)
    audit.record(
        db, audit.Action.INVENTORY_DECREMENTED, session_id=order.session_id,
        order_id=order.id, actor="system", actor_type="system",
        reason="Stock reduced after verified payment.", input_data={"lines": decremented},
        decision=audit.Decision.ALLOWED, is_synthetic=order.is_synthetic,
    )

    campaign_service.record_spend(db, order.applied_campaign_id, order.discount_paise)

    audit.record(
        db, audit.Action.ORDER_PAID, session_id=order.session_id, order_id=order.id,
        actor=actor, actor_type=actor_type,
        reason=(f"Order {order.id} finalised as PAID for "
                f"{format_inr(order.total_paise)} after server-side verification."),
        decision=audit.Decision.ALLOWED, payment_reference=order.payment_id,
        amount_paise=order.total_paise, status=OrderStatus.PAID.value,
        request_id=request_id, is_synthetic=order.is_synthetic,
    )
    db.commit()
    return _payment_result(order, transaction, verification.reason, verified=True)


def _finalize_failure(db: Session, order: Order, transaction: Transaction,
                      verification, actor: str, actor_type: str,
                      request_id: str | None) -> dict:
    if verification is None:
        target_status = PaymentStatus.VERIFICATION_FAILED
        reason = ("The payment could not be verified with the provider. The order was "
                  "NOT marked paid.")
    else:
        target_status = verification.status
        reason = verification.reason

    transaction.status = transition_payment(transaction.status, target_status).value
    transaction.verification_status = VerificationStatus.FAILED.value
    transaction.failure_reason = reason
    if verification is not None and verification.payment is not None:
        transaction.provider_payment_id = verification.payment.id
        transaction.provider_signature_present = verification.signature_valid
        transaction.provider_meta = {**(transaction.provider_meta or {}),
                                     **verification.payment.raw}

    order.status = transition_order(order.status, OrderStatus.PAYMENT_FAILED).value
    db.flush()

    action = (audit.Action.PAYMENT_VERIFICATION_FAILED
              if target_status == PaymentStatus.VERIFICATION_FAILED
              else audit.Action.PAYMENT_FAILED)
    audit.record(
        db, action, session_id=order.session_id, order_id=order.id,
        actor="payment-service", actor_type="system",
        reason=reason + " The order remains unpaid and no stock was reserved.",
        decision=audit.Decision.REJECTED,
        payment_reference=transaction.provider_payment_id,
        amount_paise=transaction.amount_paise, status=target_status.value,
        policy_result=verification.to_dict() if verification else {},
        request_id=request_id, is_synthetic=order.is_synthetic,
    )
    db.commit()
    return _payment_result(order, transaction, reason, verified=False)


def _decrement_inventory(db: Session, order: Order) -> list[dict]:
    products = catalog.get_products_by_ids(db, [i.product_id for i in order.items])
    changes = []
    for item in order.items:
        product = products.get(item.product_id)
        if product is None:
            continue
        before = product.inventory
        product.inventory = max(0, product.inventory - item.quantity)
        changes.append({"product_id": product.id, "name": product.name,
                        "before": before, "after": product.inventory,
                        "quantity": item.quantity})
    db.flush()
    return changes


def _payment_result(order: Order, transaction: Transaction, message: str, *,
                    verified: bool = False, replayed: bool = False) -> dict:
    paid = order.status == OrderStatus.PAID.value
    return {
        "order_id": order.id,
        "order_status": order.status,
        "paid": paid,
        "payment_status": transaction.status,
        "verification_status": transaction.verification_status,
        "verified": verified or transaction.verification_status == VerificationStatus.VERIFIED.value,
        "provider": transaction.provider,
        "provider_order_id": transaction.provider_order_id,
        "provider_payment_id": transaction.provider_payment_id,
        "amount_paise": transaction.amount_paise,
        "amount_display": format_inr(transaction.amount_paise),
        "message": message,
        "replayed": replayed,
        "user_message": (
            f"Payment verified. Your order is confirmed for {format_inr(order.total_paise)}."
            if paid else
            "Payment was not completed. Your order has NOT been marked as paid and "
            "nothing has been charged to you. You can retry checkout."
        ),
        "retry_available": not paid,
        "test_mode": transaction.is_test_mode,
    }


# ---------------------------------------------------------------------------
# Failure reporting and recovery
# ---------------------------------------------------------------------------
def report_payment_failure(db: Session, *, provider_order_id: str, reason: str = "",
                           actor: str = "buyer", actor_type: str = "human",
                           request_id: str | None = None) -> dict:
    """Handle a client-reported failure (checkout dismissed, card declined).

    The client's word is enough to record an *attempt*, never enough to
    conclude the payment state — the provider is still asked.
    """
    order = db.query(Order).filter(Order.payment_order_id == provider_order_id).first()
    if order is None:
        raise CheckoutError("No order matches that payment order id.",
                            code="order_not_found", http_status=404)
    transaction = (db.query(Transaction)
                   .filter(Transaction.provider_order_id == provider_order_id)
                   .order_by(Transaction.created_at.desc()).first())
    if transaction is None:
        raise CheckoutError("No transaction recorded for that payment order.",
                            code="transaction_not_found", http_status=404)

    if order.status == OrderStatus.PAID.value:
        return _payment_result(order, transaction,
                               "This order is already verified as paid; the failure "
                               "report was ignored.")

    provider = get_payment_provider()
    observed: str = ""
    try:
        payments = provider.get_payments_for_order(provider_order_id)
        captured = [p for p in payments if p.status == PaymentStatus.CAPTURED]
        if captured:
            # The client said it failed but the provider says otherwise. Trust
            # the provider, and record the discrepancy.
            observed = ("Provider reports a captured payment despite the client "
                        "reporting failure; reconciling instead.")
            log.warning("discrepancy on %s: %s", provider_order_id, observed)
            return reconcile_order(db, order.id, actor=actor, actor_type=actor_type,
                                   request_id=request_id)
        observed = (f"Provider reports {len(payments)} payment attempt(s), none captured."
                    if payments else "Provider reports no payment attempts on this order.")
    except PaymentProviderUnavailable as exc:
        observed = f"Provider unreachable while checking: {exc}"

    transaction.status = transition_payment(transaction.status, PaymentStatus.FAILED).value
    transaction.verification_status = VerificationStatus.FAILED.value
    transaction.failure_reason = (reason or "Payment was not completed.") + " " + observed
    order.status = transition_order(order.status, OrderStatus.PAYMENT_FAILED).value
    db.flush()

    audit.record(
        db, audit.Action.PAYMENT_FAILED, session_id=order.session_id, order_id=order.id,
        actor=actor, actor_type=actor_type,
        reason=(f"Payment reported as failed: {reason or 'no reason given'}. {observed} "
                f"Order left at PAYMENT_FAILED; no stock decremented, nothing charged."),
        input_data={"client_reported_reason": reason},
        decision=audit.Decision.REJECTED, payment_reference=provider_order_id,
        amount_paise=transaction.amount_paise, status=PaymentStatus.FAILED.value,
        request_id=request_id, is_synthetic=order.is_synthetic,
    )
    db.commit()
    return _payment_result(order, transaction,
                           reason or "The payment was not completed.")


def reconcile_order(db: Session, order_id: str, *, actor: str = "merchant",
                    actor_type: str = "merchant", request_id: str | None = None) -> dict:
    """Recovery action: ask the provider what really happened to this order.

    This is the escape hatch for VERIFICATION_FAILED and UNKNOWN states — the
    provider is the source of truth, and a genuinely captured payment is
    recognised here even if the browser never came back.
    """
    order = db.get(Order, order_id)
    if order is None:
        raise CheckoutError("Unknown order.", code="order_not_found", http_status=404)
    transaction = (db.query(Transaction)
                   .filter(Transaction.order_id == order.id)
                   .order_by(Transaction.created_at.desc()).first())
    if transaction is None or not transaction.provider_order_id:
        raise CheckoutError("This order has no payment to reconcile.",
                            code="nothing_to_reconcile")

    provider = get_payment_provider()
    try:
        payments = provider.get_payments_for_order(transaction.provider_order_id)
    except PaymentProviderUnavailable as exc:
        audit.record(
            db, audit.Action.PAYMENT_PROVIDER_UNAVAILABLE, session_id=order.session_id,
            order_id=order.id, actor=actor, actor_type=actor_type,
            reason=f"Reconciliation could not reach the provider: {exc}",
            decision=audit.Decision.INFO, request_id=request_id,
            is_synthetic=order.is_synthetic,
        )
        db.commit()
        raise CheckoutError(
            f"Still cannot reach the payment provider. The order remains "
            f"{order.status}. Try again shortly.",
            code="provider_unavailable", http_status=503, detail={"retryable": True},
        ) from exc

    captured = [p for p in payments
                if p.status == PaymentStatus.CAPTURED
                and p.amount_paise == transaction.amount_paise]

    if captured and order.status != OrderStatus.PAID.value:
        payment = captured[0]
        transaction.status = transition_payment(transaction.status,
                                                PaymentStatus.CAPTURED).value
        transaction.verification_status = VerificationStatus.VERIFIED.value
        transaction.provider_payment_id = payment.id
        transaction.provider_meta = {**(transaction.provider_meta or {}), **payment.raw}
        transaction.failure_reason = None
        order.payment_id = payment.id
        if order.status == OrderStatus.PAYMENT_FAILED.value:
            order.status = transition_order(order.status,
                                            OrderStatus.PAYMENT_PENDING).value
        order.status = transition_order(order.status, OrderStatus.PAID).value
        db.flush()

        audit.record(
            db, audit.Action.PAYMENT_VERIFIED, session_id=order.session_id,
            order_id=order.id, actor=actor, actor_type=actor_type,
            reason=(f"Reconciliation with {provider.display_label} found a captured "
                    f"payment {payment.id} for {format_inr(payment.amount_paise)}. "
                    "Order moved to PAID."),
            decision=audit.Decision.ALLOWED, payment_reference=payment.id,
            amount_paise=payment.amount_paise, status=PaymentStatus.CAPTURED.value,
            request_id=request_id, is_synthetic=order.is_synthetic,
        )
        _decrement_inventory(db, order)
        campaign_service.record_spend(db, order.applied_campaign_id, order.discount_paise)
        db.commit()
        return _payment_result(order, transaction,
                               "Reconciled: the provider confirms this payment was captured.",
                               verified=True)

    summary = (f"Provider reports {len(payments)} payment(s) on this order, none captured "
               f"for {format_inr(transaction.amount_paise)}.")
    audit.record(
        db, audit.Action.PAYMENT_VERIFICATION_FAILED, session_id=order.session_id,
        order_id=order.id, actor=actor, actor_type=actor_type,
        reason=f"Reconciliation found no captured payment. {summary}",
        decision=audit.Decision.REJECTED, amount_paise=transaction.amount_paise,
        status=transaction.status, request_id=request_id, is_synthetic=order.is_synthetic,
    )
    db.commit()
    return _payment_result(order, transaction, summary)


def cancel_order(db: Session, order_id: str, *, reason: str = "",
                 actor: str = "buyer", actor_type: str = "human") -> dict:
    order = db.get(Order, order_id)
    if order is None:
        raise CheckoutError("Unknown order.", code="order_not_found", http_status=404)
    try:
        order.status = transition_order(order.status, OrderStatus.CANCELLED).value
    except IllegalTransition as exc:
        raise CheckoutError(str(exc), code="illegal_transition") from exc
    db.flush()
    audit.record(db, audit.Action.ORDER_CANCELLED, session_id=order.session_id,
                 order_id=order.id, actor=actor, actor_type=actor_type,
                 reason=reason or "Order cancelled.", decision=audit.Decision.ALLOWED,
                 amount_paise=order.total_paise, is_synthetic=order.is_synthetic)
    db.commit()
    return {"order_id": order.id, "status": order.status}


__all__ = ["CheckoutError", "prepare_checkout", "confirm_and_create_payment",
           "verify_payment", "report_payment_failure", "reconcile_order",
           "cancel_order", "build_explanation"]
