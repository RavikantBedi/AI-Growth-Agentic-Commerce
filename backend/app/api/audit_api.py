"""Audit trail API.

`/api/audit/story/{order_id}` is the endpoint the challenge really asks for: it
replays one order's complete decision history and answers, for the money that
moved, what / why / who / how much / which policy / who approved / which
provider / what result / was it verified.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain.money import format_inr
from ..models import AuditEvent, CheckoutQuote, Order, Transaction

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _event_to_dict(e: AuditEvent) -> dict:
    return {
        "id": e.id,
        "created_at": e.created_at.isoformat(),
        "action": e.action,
        "actor": e.actor,
        "actor_type": e.actor_type,
        "session_id": e.session_id,
        "order_id": e.order_id,
        "request_id": e.request_id,
        "reason": e.reason,
        "input": e.input_data,
        "decision": e.decision,
        "policy_result": e.policy_result,
        "payment_reference": e.payment_reference,
        "amount_paise": e.amount_paise,
        "amount_display": format_inr(e.amount_paise) if e.amount_paise is not None else None,
        "status": e.status,
        "is_synthetic": e.is_synthetic,
    }


@router.get("/events", summary="Filterable audit log")
def events(
    db: Session = Depends(get_db),
    session_id: str | None = None,
    order_id: str | None = None,
    action: str | None = None,
    decision: str | None = None,
    actor_type: str | None = None,
    since: str | None = Query(default=None, description="ISO-8601 timestamp"),
    until: str | None = None,
    include_synthetic: bool = True,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = 0,
):
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    if session_id:
        stmt = stmt.where(AuditEvent.session_id == session_id)
    if order_id:
        stmt = stmt.where(AuditEvent.order_id == order_id)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if decision:
        stmt = stmt.where(AuditEvent.decision == decision)
    if actor_type:
        stmt = stmt.where(AuditEvent.actor_type == actor_type)
    if not include_synthetic:
        stmt = stmt.where(AuditEvent.is_synthetic.is_(False))
    for value, column, op in ((since, AuditEvent.created_at, "ge"),
                              (until, AuditEvent.created_at, "le")):
        if value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                parsed = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
            except ValueError as exc:
                raise HTTPException(400, detail={
                    "error": "invalid_timestamp",
                    "message": f"Could not parse '{value}' as ISO-8601."}) from exc
            stmt = stmt.where(column >= parsed if op == "ge" else column <= parsed)

    rows = db.scalars(stmt.limit(limit).offset(offset)).all()
    return {"events": [_event_to_dict(e) for e in rows], "count": len(rows),
            "limit": limit, "offset": offset}


@router.get("/actions", summary="Distinct action names present in the log")
def actions(db: Session = Depends(get_db)):
    rows = db.execute(
        select(AuditEvent.action, AuditEvent.decision).distinct()).all()
    return {"actions": sorted({r[0] for r in rows}),
            "decisions": sorted({r[1] for r in rows if r[1]})}


@router.get("/story/{order_id}", summary="The full explainability story for one order")
def story(order_id: str, db: Session = Depends(get_db)):
    """Replay one order's money action end to end."""
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, detail={"error": "order_not_found",
                                         "message": f"No order {order_id}."})

    events_list = db.scalars(
        select(AuditEvent).where(AuditEvent.order_id == order_id)
        .order_by(AuditEvent.created_at, AuditEvent.id)).all()
    session_events = db.scalars(
        select(AuditEvent).where(AuditEvent.session_id == order.session_id,
                                 AuditEvent.order_id.is_(None))
        .order_by(AuditEvent.created_at, AuditEvent.id)).all()
    timeline = sorted([*events_list, *session_events],
                      key=lambda e: (e.created_at, e.id))

    quote = db.scalar(
        select(CheckoutQuote).where(CheckoutQuote.order_id == order_id)
        .order_by(CheckoutQuote.created_at.desc()).limit(1))
    transactions = db.scalars(
        select(Transaction).where(Transaction.order_id == order_id)
        .order_by(Transaction.created_at)).all()
    latest = transactions[-1] if transactions else None

    def find(*actions_wanted: str) -> AuditEvent | None:
        """The most recent matching event — the outcome that stands."""
        for e in reversed(timeline):
            if e.action in actions_wanted:
                return e
        return None

    def find_first(*actions_wanted: str) -> AuditEvent | None:
        """The earliest matching event — what the shopper originally asked for.

        Follow-up turns ("add the best one", "yes") each log their own search,
        so taking the latest would report the confirmation rather than the
        request that started the order.
        """
        for e in timeline:
            if e.action in actions_wanted:
                return e
        return None

    searched = find_first("PRODUCT_SEARCHED")
    recommended = find_first("PRODUCT_RECOMMENDED")
    upsold = find("UPSELL_SUGGESTED")
    policy_event = find("POLICY_CHECKED")
    confirmation = find("PAYMENT_CONFIRMED_BY_USER")
    order_created = find("PAYMENT_ORDER_CREATED")
    verified = find("PAYMENT_VERIFIED", "PAYMENT_VERIFICATION_FAILED", "PAYMENT_FAILED")

    return {
        "order_id": order.id,
        "session_id": order.session_id,
        "status": order.status,
        "is_synthetic": order.is_synthetic,
        "narrative": {
            "what_was_requested": searched.reason if searched else
                                  "No search recorded for this order.",
            "what_the_agent_selected": recommended.reason if recommended else
                                       "Items were added directly without a recommendation step.",
            "what_was_suggested": upsold.reason if upsold else "No add-ons were suggested.",
            "what_is_being_bought": [
                {"name": i.name, "sku": i.sku, "quantity": i.quantity,
                 "unit_price_display": format_inr(i.unit_price_paise),
                 "line_total_display": format_inr(i.line_total_paise),
                 "how_it_entered_the_cart": i.source} for i in order.items],
            "how_much": {
                "subtotal": format_inr(order.subtotal_paise),
                "discount": format_inr(order.discount_paise),
                "tax": format_inr(order.tax_paise),
                "total": format_inr(order.total_paise),
                "currency": order.currency,
            },
            "which_policy": policy_event.policy_result if policy_event else {},
            "policy_verdict": policy_event.reason if policy_event else "No policy check recorded.",
            "who_approved": {
                "actor": confirmation.actor if confirmation else None,
                "actor_type": confirmation.actor_type if confirmation else None,
                "decision": confirmation.decision if confirmation else None,
                "at": confirmation.created_at.isoformat() if confirmation else None,
                "detail": confirmation.reason if confirmation else
                          "No explicit user confirmation recorded.",
            },
            "which_provider": {
                "provider": order.payment_provider,
                "provider_order_id": order.payment_order_id,
                "created": order_created.reason if order_created else None,
                "test_mode": latest.is_test_mode if latest else True,
            },
            "what_was_the_result": verified.reason if verified else
                                   "No payment outcome recorded yet.",
            "was_it_verified": {
                "verification_status": latest.verification_status if latest else "NOT_ATTEMPTED",
                "payment_status": latest.status if latest else None,
                "signature_checked": latest.provider_signature_present if latest else False,
                "order_is_paid": order.status == "PAID",
                "statement": (
                    "Verified server-side with the payment provider before the order "
                    "was marked PAID."
                    if order.status == "PAID" else
                    "This order is NOT marked paid. No unverified payment was accepted."
                ),
            },
        },
        "approved_quote": {
            "quote_id": quote.id, "total_display": format_inr(quote.total_paise),
            "explanation": quote.explanation, "cart_fingerprint": quote.cart_fingerprint,
            "confirmed_by": quote.confirmed_by,
            "confirmed_at": quote.confirmed_at.isoformat() if quote.confirmed_at else None,
            "policy_result": quote.policy_result,
        } if quote else None,
        "transactions": [{
            "transaction_id": t.id, "provider": t.provider,
            "provider_order_id": t.provider_order_id,
            "provider_payment_id": t.provider_payment_id,
            "amount_display": format_inr(t.amount_paise), "status": t.status,
            "verification_status": t.verification_status,
            "failure_reason": t.failure_reason, "is_test_mode": t.is_test_mode,
            "created_at": t.created_at.isoformat(),
        } for t in transactions],
        "timeline": [_event_to_dict(e) for e in timeline],
        "event_count": len(timeline),
    }


@router.get("/session/{session_id}", summary="Every event for one buyer session")
def session_events(session_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(AuditEvent).where(AuditEvent.session_id == session_id)
        .order_by(AuditEvent.created_at, AuditEvent.id)).all()
    return {"session_id": session_id, "events": [_event_to_dict(e) for e in rows],
            "count": len(rows)}


__all__ = ["router"]
