"""Merchant console API — catalog CRUD, settings, campaigns, metrics, simulation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..ai import tools
from ..ai.provider import provider_status, reset_llm_provider
from ..db import get_db
from ..domain import audit
from ..models import Campaign
from ..payments import get_payment_provider
from ..schemas import (CampaignCreate, CampaignDecision, CampaignStatusUpdate,
                       FailureInjectionRequest, MerchantSettingsUpdate,
                       ProductCreate, ProductUpdate, SimulationRequest)
from ..services import campaigns as campaign_service
from ..services import catalog as catalog_service
from ..services import metrics as metrics_service
from ..services import simulation as simulation_service
from ..services.merchant import get_merchant, merchant_to_dict, update_settings

router = APIRouter(prefix="/api/merchant", tags=["merchant"])


# ---------------------------------------------------------------------------
# Overview / activity
# ---------------------------------------------------------------------------
@router.get("/overview", summary="Dashboard metrics")
def overview(db: Session = Depends(get_db)):
    return metrics_service.overview(db)


@router.get("/activity", summary="Live buyer sessions")
def activity(limit: int = 25, db: Session = Depends(get_db)):
    return {"sessions": metrics_service.live_activity(db, limit=limit)}


@router.get("/metrics", summary="Metrics for a scope")
def metrics(scope: str = "all", db: Session = Depends(get_db)):
    if scope not in ("all", "live", "synthetic"):
        raise HTTPException(400, detail={"error": "invalid_scope",
                                         "message": "scope must be all, live or synthetic."})
    return metrics_service.compute(db, scope=scope).to_dict()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@router.get("/settings", summary="Merchant identity and guardrails")
def get_merchant_settings(db: Session = Depends(get_db)):
    merchant = get_merchant(db)
    db.commit()
    return merchant_to_dict(merchant)


@router.put("/settings", summary="Update merchant settings")
def put_merchant_settings(payload: MerchantSettingsUpdate, db: Session = Depends(get_db)):
    merchant = get_merchant(db)
    data = payload.model_dump(exclude_none=True)
    merchant, clamped = update_settings(db, merchant, data)
    audit.record(db, audit.Action.MERCHANT_SETTINGS_UPDATED, actor="merchant",
                 actor_type="merchant",
                 reason=("Merchant settings updated."
                         + (" Clamped: " + " ".join(clamped) if clamped else "")),
                 input_data=data, decision=audit.Decision.ALLOWED)
    db.commit()
    reset_llm_provider()
    return {**merchant_to_dict(merchant), "clamped": clamped}


# ---------------------------------------------------------------------------
# Catalog CRUD
# ---------------------------------------------------------------------------
@router.get("/products", summary="List catalog")
def list_products(db: Session = Depends(get_db), active_only: bool = False,
                  category: str | None = None, limit: int = 200, offset: int = 0):
    products, total = catalog_service.list_products(
        db, active_only=active_only, category=category, limit=min(limit, 1000),
        offset=offset)
    return {"products": [catalog_service.product_to_dict(p) for p in products],
            "total": total, "categories": catalog_service.categories(db),
            "brands": catalog_service.brands(db)}


@router.post("/products", summary="Create a product", status_code=201)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    if catalog_service.get_product_by_sku(db, payload.sku):
        raise HTTPException(409, detail={"error": "duplicate_sku",
                                         "message": f"SKU {payload.sku} already exists."})
    product = catalog_service.create_product(db, payload.model_dump())
    audit.record(db, audit.Action.CATALOG_UPDATED, actor="merchant", actor_type="merchant",
                 reason=f"Created product {product.name} ({product.sku}).",
                 input_data={"product_id": product.id}, decision=audit.Decision.ALLOWED)
    db.commit()
    return catalog_service.product_to_dict(product)


@router.put("/products/{product_id}", summary="Update a product")
def update_product(product_id: str, payload: ProductUpdate, db: Session = Depends(get_db)):
    product = catalog_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(404, detail={"error": "product_not_found",
                                         "message": f"No product {product_id}."})
    data = payload.model_dump(exclude_none=True)
    if "sku" in data and data["sku"] != product.sku:
        clash = catalog_service.get_product_by_sku(db, data["sku"])
        if clash and clash.id != product.id:
            raise HTTPException(409, detail={"error": "duplicate_sku",
                                             "message": f"SKU {data['sku']} is taken."})
    before_price = product.price_paise
    catalog_service.update_product(db, product, data)
    audit.record(
        db, audit.Action.CATALOG_UPDATED, actor="merchant", actor_type="merchant",
        reason=(f"Updated {product.name}."
                + (f" Price {before_price} -> {product.price_paise} paise."
                   if before_price != product.price_paise else "")),
        input_data=data, decision=audit.Decision.ALLOWED)
    db.commit()
    return catalog_service.product_to_dict(product)


@router.delete("/products/{product_id}", summary="Deactivate a product")
def delete_product(product_id: str, db: Session = Depends(get_db)):
    product = catalog_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(404, detail={"error": "product_not_found",
                                         "message": f"No product {product_id}."})
    catalog_service.delete_product(db, product)
    audit.record(db, audit.Action.CATALOG_UPDATED, actor="merchant", actor_type="merchant",
                 reason=f"Deactivated {product.name} ({product.sku}).",
                 input_data={"product_id": product_id}, decision=audit.Decision.ALLOWED)
    db.commit()
    return {"product_id": product_id, "active": False,
            "note": "Soft-deleted: existing orders still reference this product."}


@router.post("/seed", summary="Load the demo catalog")
def seed_catalog(db: Session = Depends(get_db), reset: bool = False):
    from ..seed import seed_database
    result = seed_database(db, reset=reset)
    db.commit()
    return result


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------
@router.get("/campaigns", summary="List campaigns")
def list_campaigns(status: str | None = None, db: Session = Depends(get_db)):
    return {"campaigns": [campaign_service.campaign_to_dict(c)
                          for c in campaign_service.list_campaigns(db, status)]}


@router.post("/campaigns", summary="Create a campaign", status_code=201)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)):
    merchant = get_merchant(db)
    campaign, notes = campaign_service.create_campaign(db, merchant, payload.model_dump())
    db.commit()
    return {**campaign_service.campaign_to_dict(campaign), "notes": notes}


@router.post("/campaigns/recommend", summary="Ask the AI to propose a campaign")
def recommend_campaign(db: Session = Depends(get_db)):
    """The AI proposes; it cannot publish.

    The proposal is created as PENDING_APPROVAL and needs an explicit merchant
    activation. The requested discount is clamped to the merchant cap here, and
    the clamp is written to the audit trail.
    """
    merchant = get_merchant(db)
    m = metrics_service.compute(db, scope="all")
    products, _ = catalog_service.list_products(db, active_only=True, limit=200)

    # Deterministic proposal from real catalog and metric data: target slow
    # movers with healthy stock. No model is needed to justify this, and the
    # rationale is auditable.
    slow_movers = sorted(products, key=lambda p: -p.inventory)[:6]
    # A deliberately over-cap request, to demonstrate the clamp end to end.
    requested_discount = merchant.max_discount_percent + 15

    proposal = {
        "name": "Overstock clearance — accessories & slow movers",
        "description": ("Targets the six highest-stock active products to convert "
                        "held inventory into revenue."),
        "target_segment": "all",
        "product_ids": [p.id for p in slow_movers],
        "discount_percent": requested_discount,
        "budget_paise": 2_000_000,
        "max_discount_paise_per_order": 300_000,
    }
    rationale = (
        f"Catalog has {len(products)} active products; the six with the highest stock "
        f"hold {sum(p.inventory for p in slow_movers)} units. Observed conversion is "
        f"{m.conversion_rate}% across {m.sessions} session(s) with an AOV of "
        f"{m.aov_paise / 100:.2f} INR. A bounded discount on held stock is the "
        f"lowest-risk lever. Requested {requested_discount}%."
    )
    campaign, notes = campaign_service.create_campaign(
        db, merchant, proposal, created_by="ai_agent", ai_rationale=rationale)
    db.commit()
    return {
        **campaign_service.campaign_to_dict(campaign),
        "notes": notes,
        "requested_discount_percent": requested_discount,
        "applied_discount_percent": campaign.discount_percent,
        "requires_merchant_approval": True,
        "note": ("This campaign is PENDING_APPROVAL. The AI cannot activate it. "
                 "Approve it explicitly to make it live."),
    }


@router.post("/campaigns/{campaign_id}/approve", summary="Approve and activate")
def approve_campaign(campaign_id: str, payload: CampaignDecision,
                     db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, detail={"error": "campaign_not_found",
                                         "message": f"No campaign {campaign_id}."})
    try:
        campaign_service.approve_campaign(db, campaign, payload.approver)
    except campaign_service.CampaignError as exc:
        raise HTTPException(400, detail={"error": exc.code, "message": exc.message}) from exc
    db.commit()
    return campaign_service.campaign_to_dict(campaign)


@router.post("/campaigns/{campaign_id}/reject", summary="Reject a proposal")
def reject_campaign(campaign_id: str, payload: CampaignDecision,
                    db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, detail={"error": "campaign_not_found",
                                         "message": f"No campaign {campaign_id}."})
    campaign_service.reject_campaign(db, campaign, payload.approver, payload.reason)
    db.commit()
    return campaign_service.campaign_to_dict(campaign)


@router.put("/campaigns/{campaign_id}/status", summary="Set campaign status")
def set_campaign_status(campaign_id: str, payload: CampaignStatusUpdate,
                        db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, detail={"error": "campaign_not_found",
                                         "message": f"No campaign {campaign_id}."})
    try:
        campaign_service.set_status(db, campaign, payload.status, payload.actor)
    except campaign_service.CampaignError as exc:
        raise HTTPException(400, detail={"error": exc.code, "message": exc.message}) from exc
    db.commit()
    return campaign_service.campaign_to_dict(campaign)


# ---------------------------------------------------------------------------
# AI configuration and boundaries
# ---------------------------------------------------------------------------
@router.get("/ai", summary="AI provider status and tool permissions")
def ai_status():
    return {"provider": provider_status(), "tools": tools.describe()}


# ---------------------------------------------------------------------------
# Growth experiment
# ---------------------------------------------------------------------------
@router.post("/simulate", summary="Run the synthetic growth experiment")
def simulate(payload: SimulationRequest, db: Session = Depends(get_db)):
    return simulation_service.run_simulation(
        db, sessions_per_arm=payload.sessions_per_arm, seed=payload.seed,
        label=payload.label)


@router.get("/experiments", summary="Past experiment runs")
def experiments(db: Session = Depends(get_db)):
    return {"experiments": simulation_service.list_experiments(db)}


# ---------------------------------------------------------------------------
# Controlled failure injection (for demonstrating graceful degradation)
# ---------------------------------------------------------------------------
@router.get("/failure-injection", summary="Current failure injection state")
def get_failure_injection():
    return get_payment_provider().failure_mode()


@router.post("/failure-injection", summary="Enable/disable payment failure modes")
def set_failure_injection(payload: FailureInjectionRequest,
                          db: Session = Depends(get_db)):
    """Adds failures only — it can never turn a failed payment into a success."""
    provider = get_payment_provider()
    mode = provider.set_failure_mode(outage=payload.outage,
                                     verification_failure=payload.verification_failure)
    audit.record(db, audit.Action.MERCHANT_SETTINGS_UPDATED, actor="merchant",
                 actor_type="merchant",
                 reason=f"Payment failure injection set to {mode}.",
                 input_data=mode, decision=audit.Decision.INFO)
    db.commit()
    return {**mode, "provider": provider.name,
            "note": ("Failure injection can only cause failures. There is no setting "
                     "that fakes a successful payment.")}


__all__ = ["router"]
