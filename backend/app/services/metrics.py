"""Revenue and growth metrics.

Every figure is computed from rows this application actually wrote. Nothing is
hardcoded. Synthetic (simulation-generated) data is tracked separately from
live demo data and every response is explicitly labelled, so a number produced
by the simulator can never be read as real merchant revenue.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.money import format_inr
from ..domain.states import OrderStatus
from ..models import AuditEvent, BuyerSession, Order, Transaction


@dataclass
class Metrics:
    scope: str
    sessions: int = 0
    converting_sessions: int = 0
    orders_created: int = 0
    paid_orders: int = 0
    failed_payments: int = 0
    gmv_paise: int = 0
    upsell_revenue_paise: int = 0
    cross_sell_revenue_paise: int = 0
    discount_given_paise: int = 0
    carts_with_items: int = 0
    abandoned_carts: int = 0
    #: Sessions where any bounded add-on was offered. Both the upsell and the
    #: cross-sell acceptance rates are measured against this denominator.
    upsell_suggested_sessions: int = 0
    upsell_accepted_sessions: int = 0
    cross_sell_accepted_sessions: int = 0
    addon_accepted_sessions: int = 0

    @property
    def conversion_rate(self) -> float:
        return round(self.converting_sessions / self.sessions * 100, 2) if self.sessions else 0.0

    @property
    def aov_paise(self) -> int:
        return round(self.gmv_paise / self.paid_orders) if self.paid_orders else 0

    @property
    def revenue_per_session_paise(self) -> int:
        return round(self.gmv_paise / self.sessions) if self.sessions else 0

    @property
    def upsell_acceptance_rate(self) -> float:
        if not self.upsell_suggested_sessions:
            return 0.0
        return round(self.upsell_accepted_sessions / self.upsell_suggested_sessions * 100, 2)

    @property
    def cross_sell_acceptance_rate(self) -> float:
        if not self.upsell_suggested_sessions:
            return 0.0
        return round(self.cross_sell_accepted_sessions / self.upsell_suggested_sessions * 100, 2)

    @property
    def addon_acceptance_rate(self) -> float:
        """Sessions that accepted *any* add-on, upsell or cross-sell.

        The headline growth number: whether a suggestion was classified as an
        upsell or a cross-sell depends on which catalog relationship produced
        it, which is an implementation detail the merchant should not have to
        add up by hand.
        """
        if not self.upsell_suggested_sessions:
            return 0.0
        return round(self.addon_accepted_sessions / self.upsell_suggested_sessions * 100, 2)

    @property
    def cart_abandonment_rate(self) -> float:
        if not self.carts_with_items:
            return 0.0
        return round(self.abandoned_carts / self.carts_with_items * 100, 2)

    @property
    def payment_failure_rate(self) -> float:
        attempts = self.paid_orders + self.failed_payments
        return round(self.failed_payments / attempts * 100, 2) if attempts else 0.0

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "is_synthetic": self.scope == "synthetic",
            "label": {"synthetic": "SYNTHETIC / DEMO DATA",
                      "live": "LIVE TEST-MODE DATA",
                      "all": "ALL DATA (test mode)"}[self.scope],
            "sessions": self.sessions,
            "converting_sessions": self.converting_sessions,
            "orders_created": self.orders_created,
            "paid_orders": self.paid_orders,
            "failed_payments": self.failed_payments,
            "conversion_rate_percent": self.conversion_rate,
            "gmv_paise": self.gmv_paise,
            "gmv_display": format_inr(self.gmv_paise),
            "aov_paise": self.aov_paise,
            "aov_display": format_inr(self.aov_paise),
            "revenue_per_session_paise": self.revenue_per_session_paise,
            "revenue_per_session_display": format_inr(self.revenue_per_session_paise),
            "upsell_revenue_paise": self.upsell_revenue_paise,
            "upsell_revenue_display": format_inr(self.upsell_revenue_paise),
            "cross_sell_revenue_paise": self.cross_sell_revenue_paise,
            "cross_sell_revenue_display": format_inr(self.cross_sell_revenue_paise),
            "discount_given_paise": self.discount_given_paise,
            "discount_given_display": format_inr(self.discount_given_paise),
            "upsell_suggested_sessions": self.upsell_suggested_sessions,
            "upsell_accepted_sessions": self.upsell_accepted_sessions,
            "addon_accepted_sessions": self.addon_accepted_sessions,
            "upsell_acceptance_rate_percent": self.upsell_acceptance_rate,
            "cross_sell_acceptance_rate_percent": self.cross_sell_acceptance_rate,
            "addon_acceptance_rate_percent": self.addon_acceptance_rate,
            "carts_with_items": self.carts_with_items,
            "abandoned_carts": self.abandoned_carts,
            "cart_abandonment_rate_percent": self.cart_abandonment_rate,
            "payment_failure_rate_percent": self.payment_failure_rate,
        }


def compute(db: Session, *, scope: str = "all",
            experiment_id: str | None = None,
            variant: str | None = None) -> Metrics:
    """Aggregate metrics for a scope: 'all', 'live' (non-synthetic) or 'synthetic'."""
    m = Metrics(scope=scope)

    session_filters = []
    if scope == "live":
        session_filters.append(BuyerSession.is_synthetic.is_(False))
    elif scope == "synthetic":
        session_filters.append(BuyerSession.is_synthetic.is_(True))
    if experiment_id:
        session_filters.append(BuyerSession.experiment_id == experiment_id)
    if variant:
        session_filters.append(BuyerSession.variant == variant)

    session_ids = list(db.scalars(
        select(BuyerSession.id).where(*session_filters) if session_filters
        else select(BuyerSession.id)
    ).all())
    m.sessions = len(session_ids)
    if not session_ids:
        return m

    orders = list(db.scalars(
        select(Order).where(Order.session_id.in_(session_ids))).all())
    m.orders_created = len(orders)

    paid = [o for o in orders if o.status == OrderStatus.PAID.value]
    m.paid_orders = len(paid)
    m.gmv_paise = sum(o.total_paise for o in paid)
    m.upsell_revenue_paise = sum(o.upsell_revenue_paise for o in paid)
    m.cross_sell_revenue_paise = sum(o.cross_sell_revenue_paise for o in paid)
    m.discount_given_paise = sum(o.discount_paise for o in paid)
    m.converting_sessions = len({o.session_id for o in paid})

    m.failed_payments = db.scalar(
        select(func.count()).select_from(Transaction)
        .join(Order, Order.id == Transaction.order_id)
        .where(Order.session_id.in_(session_ids),
               Transaction.status.in_(["FAILED", "VERIFICATION_FAILED"]))
    ) or 0

    with_items = [o for o in orders if o.items]
    m.carts_with_items = len({o.session_id for o in with_items})
    paid_sessions = {o.session_id for o in paid}
    m.abandoned_carts = len({o.session_id for o in with_items} - paid_sessions)

    suggested = set(db.scalars(
        select(AuditEvent.session_id).where(
            AuditEvent.action == "UPSELL_SUGGESTED",
            AuditEvent.session_id.in_(session_ids))
    ).all())
    accepted = set(db.scalars(
        select(AuditEvent.session_id).where(
            AuditEvent.action == "UPSELL_ACCEPTED",
            AuditEvent.session_id.in_(session_ids))
    ).all())
    cross_accepted = set(db.scalars(
        select(AuditEvent.session_id).where(
            AuditEvent.action == "CROSS_SELL_ACCEPTED",
            AuditEvent.session_id.in_(session_ids))
    ).all())
    m.upsell_suggested_sessions = len(suggested)
    m.upsell_accepted_sessions = len(accepted)
    m.cross_sell_accepted_sessions = len(cross_accepted)
    m.addon_accepted_sessions = len(accepted | cross_accepted)
    return m


def overview(db: Session) -> dict:
    """Everything the dashboard's Overview tab needs, in one round trip."""
    live = compute(db, scope="live")
    synthetic = compute(db, scope="synthetic")
    combined = compute(db, scope="all")

    recent_paid = list(db.scalars(
        select(Order).where(Order.status == OrderStatus.PAID.value)
        .order_by(Order.updated_at.desc()).limit(10)
    ).all())

    return {
        "live": live.to_dict(),
        "synthetic": synthetic.to_dict(),
        "combined": combined.to_dict(),
        "recent_paid_orders": [
            {"order_id": o.id, "total_display": format_inr(o.total_paise),
             "total_paise": o.total_paise, "is_synthetic": o.is_synthetic,
             "payment_id": o.payment_id, "updated_at": o.updated_at.isoformat()}
            for o in recent_paid
        ],
        "disclaimer": (
            "All payments are TEST MODE. 'Live' here means real interactions with this "
            "running application, not real money. 'Synthetic' rows were produced by the "
            "growth simulator and are labelled as such everywhere they appear."
        ),
    }


def live_activity(db: Session, limit: int = 25) -> list[dict]:
    """Recent buyer sessions with their current commerce state."""
    sessions = list(db.scalars(
        select(BuyerSession).order_by(BuyerSession.created_at.desc()).limit(limit)
    ).all())
    out = []
    for s in sessions:
        orders = list(db.scalars(
            select(Order).where(Order.session_id == s.id)
            .order_by(Order.created_at.desc())).all())
        latest = orders[0] if orders else None
        last_intent = db.scalar(
            select(AuditEvent.reason).where(
                AuditEvent.session_id == s.id,
                AuditEvent.action.in_(["PRODUCT_SEARCHED", "PRODUCT_RECOMMENDED"]))
            .order_by(AuditEvent.created_at.desc()).limit(1)
        )
        out.append({
            "session_id": s.id,
            "actor_type": s.actor_type,
            "actor_label": s.actor_label,
            "channel": s.channel,
            "variant": s.variant,
            "is_synthetic": s.is_synthetic,
            "created_at": s.created_at.isoformat(),
            "last_intent": (last_intent or "")[:200],
            "order_id": latest.id if latest else None,
            "order_status": latest.status if latest else None,
            "items": len(latest.items) if latest else 0,
            "total_paise": latest.total_paise if latest else 0,
            "total_display": format_inr(latest.total_paise) if latest else format_inr(0),
            "payment_id": latest.payment_id if latest else None,
        })
    return out


__all__ = ["Metrics", "compute", "overview", "live_activity"]
