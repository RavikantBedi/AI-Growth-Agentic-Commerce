"""Audit trail.

Every money-relevant action appends an immutable event. The dashboard replays
these to answer, for any payment: what, why, who, how much, which policy, who
approved, which provider, what result, and was it verified.

Nothing sensitive is ever written here — `redact()` strips anything that looks
like a card number, CVV, PIN, or API secret before it reaches the database.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditEvent

log = logging.getLogger("audit")


class Action:
    """Canonical audit action names."""
    SESSION_STARTED = "SESSION_STARTED"
    PRODUCT_SEARCHED = "PRODUCT_SEARCHED"
    PRODUCT_VIEWED = "PRODUCT_VIEWED"
    PRODUCT_RECOMMENDED = "PRODUCT_RECOMMENDED"
    UPSELL_SUGGESTED = "UPSELL_SUGGESTED"
    UPSELL_ACCEPTED = "UPSELL_ACCEPTED"
    UPSELL_DECLINED = "UPSELL_DECLINED"
    CROSS_SELL_SUGGESTED = "CROSS_SELL_SUGGESTED"
    CROSS_SELL_ACCEPTED = "CROSS_SELL_ACCEPTED"
    CART_UPDATED = "CART_UPDATED"
    CART_CLEARED = "CART_CLEARED"
    PRICE_CALCULATED = "PRICE_CALCULATED"
    POLICY_CHECKED = "POLICY_CHECKED"
    INVENTORY_CHECKED = "INVENTORY_CHECKED"
    INVENTORY_DECREMENTED = "INVENTORY_DECREMENTED"
    PAYMENT_CONFIRMATION_REQUESTED = "PAYMENT_CONFIRMATION_REQUESTED"
    PAYMENT_CONFIRMED_BY_USER = "PAYMENT_CONFIRMED_BY_USER"
    PAYMENT_ORDER_CREATED = "PAYMENT_ORDER_CREATED"
    PAYMENT_ATTEMPTED = "PAYMENT_ATTEMPTED"
    PAYMENT_VERIFIED = "PAYMENT_VERIFIED"
    PAYMENT_VERIFICATION_FAILED = "PAYMENT_VERIFICATION_FAILED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_PROVIDER_UNAVAILABLE = "PAYMENT_PROVIDER_UNAVAILABLE"
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_PAID = "ORDER_PAID"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_STATE_CHANGED = "ORDER_STATE_CHANGED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    AI_REQUEST = "AI_REQUEST"
    AI_RESPONSE_REJECTED = "AI_RESPONSE_REJECTED"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    PROMPT_INJECTION_DETECTED = "PROMPT_INJECTION_DETECTED"
    CAMPAIGN_RECOMMENDED = "CAMPAIGN_RECOMMENDED"
    CAMPAIGN_APPROVED = "CAMPAIGN_APPROVED"
    CAMPAIGN_REJECTED = "CAMPAIGN_REJECTED"
    CAMPAIGN_ACTIVATED = "CAMPAIGN_ACTIVATED"
    DISCOUNT_REJECTED = "DISCOUNT_REJECTED"
    MERCHANT_SETTINGS_UPDATED = "MERCHANT_SETTINGS_UPDATED"
    CATALOG_UPDATED = "CATALOG_UPDATED"
    EXPERIMENT_RUN = "EXPERIMENT_RUN"


class Decision:
    ALLOWED = "ALLOWED"
    REJECTED = "REJECTED"
    INFO = "INFO"


#: Keys whose values must never be persisted.
_SENSITIVE_KEYS = re.compile(
    r"(card|cvv|cvc|pin|password|passwd|secret|key_secret|authorization|"
    r"token|otp|account_number|ifsc|upi_pin)",
    re.IGNORECASE,
)
_PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively strip secrets and card-shaped numbers from audit payloads."""
    if _depth > 6:
        return "<max-depth>"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _SENSITIVE_KEYS.search(str(k)):
                out[k] = "<redacted>"
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth + 1) for v in value][:200]
    if isinstance(value, str):
        if len(value) > 2000:
            value = value[:2000] + "…<truncated>"
        return _PAN_RE.sub("<redacted-number>", value)
    return value


def record(
    db: Session,
    action: str,
    *,
    actor: str = "system",
    actor_type: str = "system",
    session_id: str | None = None,
    order_id: str | None = None,
    request_id: str | None = None,
    reason: str = "",
    input_data: dict | None = None,
    decision: str = Decision.INFO,
    policy_result: dict | None = None,
    payment_reference: str | None = None,
    amount_paise: int | None = None,
    status: str = "",
    is_synthetic: bool = False,
    flush: bool = True,
) -> AuditEvent:
    """Append one audit event. Callers commit as part of their own transaction."""
    event = AuditEvent(
        action=action,
        actor=actor,
        actor_type=actor_type,
        session_id=session_id,
        order_id=order_id,
        request_id=request_id,
        reason=reason[:4000] if reason else "",
        input_data=redact(input_data or {}),
        decision=decision,
        policy_result=redact(policy_result or {}),
        payment_reference=payment_reference,
        amount_paise=amount_paise,
        status=status,
        is_synthetic=is_synthetic,
    )
    db.add(event)
    if flush:
        db.flush()
    log.info(
        "audit action=%s decision=%s session=%s order=%s amount_paise=%s status=%s",
        action, decision, session_id, order_id, amount_paise, status,
    )
    return event


__all__ = ["Action", "Decision", "record", "redact"]
