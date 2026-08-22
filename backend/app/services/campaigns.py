"""Campaign orchestrator.

The AI may *recommend* a campaign; it can never publish one. An AI-authored
campaign is always created in PENDING_APPROVAL and needs an explicit merchant
approval to become ACTIVE:

    AI recommendation -> merchant review -> merchant approval -> activation

Every discount is bounded twice: by the merchant's `max_discount_percent` and
by the deployment's `MAX_DISCOUNT_PERCENT`. A request above either is recorded
as REJECTED and clamped, never silently applied.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..domain import audit
from ..domain.money import format_inr, percent_of
from ..domain.policy import clamp_discount
from ..models import Campaign, Merchant
from .merchant import build_policy

log = logging.getLogger("services.campaigns")


class CampaignStatus:
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"
    REJECTED = "REJECTED"


class CampaignError(Exception):
    def __init__(self, message: str, *, code: str = "campaign_error"):
        super().__init__(message)
        self.message, self.code = message, code


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def campaign_to_dict(c: Campaign) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "target_segment": c.target_segment,
        "product_ids": c.product_ids or [],
        "discount_percent": c.discount_percent,
        "starts_at": c.starts_at.isoformat() if c.starts_at else None,
        "ends_at": c.ends_at.isoformat() if c.ends_at else None,
        "budget_paise": c.budget_paise,
        "budget_display": format_inr(c.budget_paise),
        "spent_paise": c.spent_paise,
        "spent_display": format_inr(c.spent_paise),
        "remaining_paise": max(0, c.budget_paise - c.spent_paise),
        "max_discount_paise_per_order": c.max_discount_paise_per_order,
        "status": c.status,
        "created_by": c.created_by,
        "approved_by": c.approved_by,
        "approved_at": c.approved_at.isoformat() if c.approved_at else None,
        "ai_rationale": c.ai_rationale,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def list_campaigns(db: Session, status: str | None = None) -> list[Campaign]:
    stmt = select(Campaign).order_by(Campaign.created_at.desc())
    if status:
        stmt = stmt.where(Campaign.status == status)
    return list(db.scalars(stmt).all())


def create_campaign(db: Session, merchant: Merchant, data: dict, *,
                    created_by: str = "merchant",
                    ai_rationale: str = "") -> tuple[Campaign, list[str]]:
    """Create a campaign with every limit enforced at write time."""
    policy = build_policy(merchant)
    notes: list[str] = []

    requested = float(data.get("discount_percent", 0) or 0)
    effective, was_clamped, explanation = clamp_discount(requested, policy)
    if was_clamped:
        notes.append(explanation)
        audit.record(
            db, audit.Action.DISCOUNT_REJECTED,
            actor=created_by, actor_type="ai_agent" if created_by == "ai_agent" else "merchant",
            reason=explanation, decision=audit.Decision.REJECTED,
            input_data={"requested_percent": requested,
                        "campaign_name": data.get("name", "")},
            policy_result={"max_discount_percent": policy.max_discount_percent,
                           "requested": requested, "applied": effective},
        )

    budget = int(data.get("budget_paise", 0) or 0)
    if budget > settings.max_campaign_budget_paise:
        notes.append(
            f"Budget clamped from {format_inr(budget)} to the deployment cap of "
            f"{format_inr(settings.max_campaign_budget_paise)} (MAX_CAMPAIGN_BUDGET)."
        )
        budget = settings.max_campaign_budget_paise

    # An AI-authored campaign can never be born ACTIVE.
    requested_status = str(data.get("status", CampaignStatus.DRAFT)).upper()
    if created_by == "ai_agent":
        status = CampaignStatus.PENDING_APPROVAL
        if requested_status == CampaignStatus.ACTIVE:
            notes.append("An AI-proposed campaign cannot be activated directly; "
                         "it was created as PENDING_APPROVAL for merchant review.")
    elif requested_status not in (CampaignStatus.DRAFT, CampaignStatus.PENDING_APPROVAL):
        status = CampaignStatus.DRAFT
        notes.append("New campaigns start as DRAFT; activate explicitly after review.")
    else:
        status = requested_status

    campaign = Campaign(
        name=data.get("name", "Untitled campaign"),
        description=data.get("description", ""),
        target_segment=data.get("target_segment", "all"),
        product_ids=data.get("product_ids") or [],
        discount_percent=effective,
        starts_at=_parse_dt(data.get("starts_at")),
        ends_at=_parse_dt(data.get("ends_at")),
        budget_paise=budget,
        max_discount_paise_per_order=int(data.get("max_discount_paise_per_order", 0) or 0),
        status=status,
        created_by=created_by,
        ai_rationale=ai_rationale,
    )
    db.add(campaign)
    db.flush()

    audit.record(
        db,
        audit.Action.CAMPAIGN_RECOMMENDED if created_by == "ai_agent"
        else audit.Action.CAMPAIGN_APPROVED,
        actor=created_by,
        actor_type="ai_agent" if created_by == "ai_agent" else "merchant",
        reason=(f"Campaign '{campaign.name}' created as {status} with "
                f"{effective}% discount." + (" " + " ".join(notes) if notes else "")),
        input_data={"requested": data, "notes": notes},
        decision=audit.Decision.ALLOWED,
        policy_result={"max_discount_percent": policy.max_discount_percent,
                       "applied_discount_percent": effective},
    )
    return campaign, notes


def approve_campaign(db: Session, campaign: Campaign, approver: str) -> Campaign:
    if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.PENDING_APPROVAL,
                               CampaignStatus.PAUSED):
        raise CampaignError(f"A {campaign.status} campaign cannot be activated.",
                            code="invalid_status")
    campaign.status = CampaignStatus.ACTIVE
    campaign.approved_by = approver
    campaign.approved_at = _now()
    db.flush()
    audit.record(db, audit.Action.CAMPAIGN_ACTIVATED, actor=approver, actor_type="merchant",
                 reason=(f"Merchant '{approver}' approved and activated campaign "
                         f"'{campaign.name}' ({campaign.discount_percent}% off)."),
                 decision=audit.Decision.ALLOWED,
                 input_data={"campaign_id": campaign.id})
    return campaign


def reject_campaign(db: Session, campaign: Campaign, approver: str, reason: str = "") -> Campaign:
    campaign.status = CampaignStatus.REJECTED
    campaign.approved_by = approver
    campaign.approved_at = _now()
    db.flush()
    audit.record(db, audit.Action.CAMPAIGN_REJECTED, actor=approver, actor_type="merchant",
                 reason=f"Merchant rejected campaign '{campaign.name}'. {reason}".strip(),
                 decision=audit.Decision.REJECTED,
                 input_data={"campaign_id": campaign.id})
    return campaign


def set_status(db: Session, campaign: Campaign, status: str, actor: str) -> Campaign:
    valid = {CampaignStatus.DRAFT, CampaignStatus.PENDING_APPROVAL, CampaignStatus.ACTIVE,
             CampaignStatus.PAUSED, CampaignStatus.ENDED, CampaignStatus.REJECTED}
    if status not in valid:
        raise CampaignError(f"Unknown campaign status '{status}'.", code="invalid_status")
    if status == CampaignStatus.ACTIVE:
        return approve_campaign(db, campaign, actor)
    campaign.status = status
    db.flush()
    audit.record(db, audit.Action.CAMPAIGN_APPROVED, actor=actor, actor_type="merchant",
                 reason=f"Campaign '{campaign.name}' set to {status}.",
                 decision=audit.Decision.INFO, input_data={"campaign_id": campaign.id})
    return campaign


def active_campaigns(db: Session) -> list[Campaign]:
    now = _now()
    result = []
    for c in db.scalars(select(Campaign).where(Campaign.status == CampaignStatus.ACTIVE)).all():
        if c.starts_at and c.starts_at > now:
            continue
        if c.ends_at and c.ends_at < now:
            continue
        if c.budget_paise and c.spent_paise >= c.budget_paise:
            continue
        result.append(c)
    return result


def resolve_discount(db: Session, items: list[dict],
                     merchant: Merchant) -> tuple[float, str, str | None, int | None]:
    """Pick the best applicable campaign for a cart.

    Returns `(discount_percent_of_subtotal, label, campaign_id, max_discount_paise)`.
    The percentage is expressed against the whole subtotal so pricing stays a
    single calculation, while the rupee cap keeps the discount limited to the
    campaign's eligible products, its per-order cap and its remaining budget.
    """
    subtotal = sum(int(i["unit_price_paise"]) * int(i["quantity"]) for i in items)
    if subtotal <= 0:
        return 0.0, "", None, None

    policy = build_policy(merchant)
    best: tuple[int, Campaign, int] | None = None  # (discount_paise, campaign, eligible)

    for campaign in active_campaigns(db):
        eligible = subtotal if not campaign.product_ids else sum(
            int(i["unit_price_paise"]) * int(i["quantity"])
            for i in items if i["product_id"] in (campaign.product_ids or [])
        )
        if eligible <= 0:
            continue

        effective_percent, _, _ = clamp_discount(campaign.discount_percent, policy)
        discount = percent_of(eligible, effective_percent)
        if campaign.max_discount_paise_per_order:
            discount = min(discount, campaign.max_discount_paise_per_order)
        if campaign.budget_paise:
            discount = min(discount, max(0, campaign.budget_paise - campaign.spent_paise))
        if discount <= 0:
            continue
        if best is None or discount > best[0]:
            best = (discount, campaign, eligible)

    if best is None:
        return 0.0, "", None, None

    discount_paise, campaign, _ = best
    # Hard ceiling: the discount can never exceed the merchant's cap on the
    # whole cart, whatever a campaign says.
    ceiling = percent_of(subtotal, policy.max_discount_percent)
    discount_paise = min(discount_paise, ceiling)

    percent_of_subtotal = round(discount_paise * 100 / subtotal, 6)
    label = f"{campaign.name} ({campaign.discount_percent:g}% off eligible items)"
    return percent_of_subtotal, label, campaign.id, discount_paise


def record_spend(db: Session, campaign_id: str | None, discount_paise: int) -> None:
    """Charge a realised discount against the campaign budget after payment."""
    if not campaign_id or discount_paise <= 0:
        return
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return
    campaign.spent_paise += discount_paise
    if campaign.budget_paise and campaign.spent_paise >= campaign.budget_paise:
        campaign.status = CampaignStatus.ENDED
        audit.record(db, audit.Action.CAMPAIGN_APPROVED, actor="system", actor_type="system",
                     reason=(f"Campaign '{campaign.name}' reached its budget of "
                             f"{format_inr(campaign.budget_paise)} and was ended."),
                     decision=audit.Decision.INFO,
                     input_data={"campaign_id": campaign.id})
    db.flush()


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


__all__ = ["CampaignStatus", "CampaignError", "campaign_to_dict", "list_campaigns",
           "create_campaign", "approve_campaign", "reject_campaign", "set_status",
           "active_campaigns", "resolve_discount", "record_spend"]
