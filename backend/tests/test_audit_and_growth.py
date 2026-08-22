"""Audit completeness, campaign governance, metrics and the growth experiment."""
from __future__ import annotations

import pytest

from .conftest import product_id


def _complete_purchase(client, session_id, sku="LAP-DEV-001", outcome="success"):
    pid = product_id(client, sku)
    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1})
    quote = client.post("/api/payments/prepare", json={"session_id": session_id}).json()
    payment = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": True,
        "confirmed_by": "audit-test-buyer"}).json()
    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": outcome}).json()
    result = client.post("/api/payments/verify", json={
        "razorpay_order_id": attempt["provider_order_id"],
        "razorpay_payment_id": attempt["provider_payment_id"],
        "razorpay_signature": attempt["signature"]}).json()
    return quote, result


# ---------------------------------------------------------------------------
# Audit completeness
# ---------------------------------------------------------------------------
def test_every_money_action_is_recorded(client, session_id):
    quote, _ = _complete_purchase(client, session_id)
    actions = {e["action"] for e in
               client.get(f"/api/audit/story/{quote['order_id']}").json()["timeline"]}

    required = {
        "ORDER_CREATED", "CART_UPDATED", "PRICE_CALCULATED", "INVENTORY_CHECKED",
        "POLICY_CHECKED", "PAYMENT_CONFIRMATION_REQUESTED",
        "PAYMENT_CONFIRMED_BY_USER", "PAYMENT_ORDER_CREATED", "PAYMENT_ATTEMPTED",
        "PAYMENT_VERIFIED", "INVENTORY_DECREMENTED", "ORDER_PAID",
    }
    assert required <= actions, f"missing: {required - actions}"


def test_the_audit_story_answers_every_required_question(client, session_id):
    client.post("/api/buyer/chat", json={
        "session_id": session_id,
        "message": "I need a laptop for programming under ₹80,000"})
    client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "Add the best one"})
    client.post("/api/buyer/chat", json={"session_id": session_id, "message": "Yes"})

    quote = client.post("/api/payments/prepare", json={"session_id": session_id}).json()
    payment = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": True,
        "confirmed_by": "priya"}).json()
    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": "success"}).json()
    client.post("/api/payments/verify", json={
        "razorpay_order_id": attempt["provider_order_id"],
        "razorpay_payment_id": attempt["provider_payment_id"],
        "razorpay_signature": attempt["signature"]})

    narrative = client.get(
        f"/api/audit/story/{quote['order_id']}").json()["narrative"]

    assert narrative["what_was_requested"]                      # WHAT
    assert narrative["what_the_agent_selected"]                 # WHY
    assert narrative["who_approved"]["actor"] == "priya"        # WHO
    assert narrative["how_much"]["total"].startswith("₹")       # HOW MUCH
    assert narrative["which_policy"]["allowed"] is True         # WHICH POLICY
    assert narrative["who_approved"]["decision"] == "ALLOWED"   # WHO APPROVED
    assert narrative["which_provider"]["provider"]              # WHAT PROVIDER
    assert narrative["what_was_the_result"]                     # WHAT RESULT
    assert narrative["was_it_verified"]["verification_status"] == "VERIFIED"

    for line in narrative["what_is_being_bought"]:
        assert line["how_it_entered_the_cart"] in ("direct", "upsell", "cross_sell")


def test_the_approved_quote_is_preserved_verbatim(client, session_id):
    quote, _ = _complete_purchase(client, session_id)
    approved = client.get(f"/api/audit/story/{quote['order_id']}").json()["approved_quote"]

    assert approved["confirmed_by"] == "audit-test-buyer"
    assert approved["confirmed_at"] is not None
    assert "You are about to pay" in approved["explanation"]
    assert approved["cart_fingerprint"]


def test_a_failed_payment_is_audited_as_rejected(client, session_id):
    quote, result = _complete_purchase(client, session_id, sku="ACC-STD-013",
                                       outcome="failure")
    assert result["paid"] is False

    events = client.get("/api/audit/events", params={
        "order_id": quote["order_id"], "action": "PAYMENT_FAILED"}).json()
    assert events["count"] >= 1
    assert events["events"][0]["decision"] == "REJECTED"
    assert events["events"][0]["reason"]


def test_audit_events_can_be_filtered(client, session_id):
    _complete_purchase(client, session_id)

    by_session = client.get("/api/audit/events",
                            params={"session_id": session_id}).json()
    assert by_session["count"] > 0
    assert all(e["session_id"] == session_id for e in by_session["events"])

    by_action = client.get("/api/audit/events",
                           params={"action": "PAYMENT_VERIFIED"}).json()
    assert all(e["action"] == "PAYMENT_VERIFIED" for e in by_action["events"])

    by_decision = client.get("/api/audit/events",
                             params={"decision": "ALLOWED", "limit": 10}).json()
    assert all(e["decision"] == "ALLOWED" for e in by_decision["events"])

    listed = client.get("/api/audit/actions").json()
    assert "PAYMENT_VERIFIED" in listed["actions"]


def test_audit_rejects_a_malformed_timestamp_filter(client):
    resp = client.get("/api/audit/events", params={"since": "not-a-date"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_timestamp"


def test_every_event_carries_a_request_id_when_made_over_http(client, session_id):
    _complete_purchase(client, session_id)
    events = client.get("/api/audit/events", params={
        "session_id": session_id, "action": "POLICY_CHECKED"}).json()["events"]
    assert any(e["request_id"] for e in events)


# ---------------------------------------------------------------------------
# Campaign governance
# ---------------------------------------------------------------------------
def test_ai_proposed_campaign_needs_merchant_approval(client):
    proposal = client.post("/api/merchant/campaigns/recommend").json()

    assert proposal["status"] == "PENDING_APPROVAL"
    assert proposal["created_by"] == "ai_agent"
    assert proposal["requires_merchant_approval"] is True
    assert proposal["ai_rationale"]

    # The over-cap discount request was clamped and explained.
    assert proposal["requested_discount_percent"] == 35
    assert proposal["applied_discount_percent"] == 20
    assert any("REJECTED" in note for note in proposal["notes"])

    rejections = client.get("/api/audit/events",
                            params={"action": "DISCOUNT_REJECTED"}).json()
    assert rejections["count"] >= 1
    assert rejections["events"][0]["decision"] == "REJECTED"

    # The proposal does nothing until a merchant activates it.
    active = client.get("/api/merchant/campaigns", params={"status": "ACTIVE"}).json()
    assert all(c["id"] != proposal["id"] for c in active["campaigns"])

    approved = client.post(f"/api/merchant/campaigns/{proposal['id']}/approve",
                           json={"approver": "merchant-owner"}).json()
    assert approved["status"] == "ACTIVE"
    assert approved["approved_by"] == "merchant-owner"

    events = client.get("/api/audit/events",
                        params={"action": "CAMPAIGN_ACTIVATED"}).json()
    assert events["count"] >= 1

    client.put(f"/api/merchant/campaigns/{proposal['id']}/status",
               json={"status": "ENDED", "actor": "merchant-owner"})


def test_a_rejected_campaign_never_activates(client):
    proposal = client.post("/api/merchant/campaigns/recommend").json()
    rejected = client.post(f"/api/merchant/campaigns/{proposal['id']}/reject",
                           json={"approver": "merchant-owner",
                                 "reason": "Margins too thin."}).json()
    assert rejected["status"] == "REJECTED"

    resp = client.post(f"/api/merchant/campaigns/{proposal['id']}/approve",
                       json={"approver": "merchant-owner"})
    assert resp.status_code == 400


def test_an_active_campaign_discount_is_applied_and_capped(client, session_id):
    pid = product_id(client, "AUD-HED-031")
    campaign = client.post("/api/merchant/campaigns", json={
        "name": "Audio week", "product_ids": [pid], "discount_percent": 10,
        "budget_paise": 1_000_000}).json()
    client.post(f"/api/merchant/campaigns/{campaign['id']}/approve",
                json={"approver": "merchant-owner"})
    try:
        cart = client.post("/api/buyer/cart/add", json={
            "session_id": session_id, "product_id": pid, "quantity": 1}).json()

        assert cart["discount_paise"] > 0
        assert cart["campaign_id"] == campaign["id"]
        assert "Audio week" in cart["discount_label"]
        # Never more than the merchant's 20% cap.
        assert cart["discount_paise"] <= round(cart["subtotal_paise"] * 0.20)
        assert cart["discount_paise"] == round(cart["subtotal_paise"] * 0.10)
    finally:
        client.put(f"/api/merchant/campaigns/{campaign['id']}/status",
                   json={"status": "ENDED", "actor": "merchant-owner"})


def test_campaign_budget_is_clamped_to_the_deployment_cap(client):
    campaign = client.post("/api/merchant/campaigns", json={
        "name": "Huge budget", "discount_percent": 5,
        "budget_paise": 999_999_999}).json()
    assert campaign["budget_paise"] == 5_000_000
    assert any("clamped" in note.lower() for note in campaign["notes"])


def test_new_campaigns_start_inactive(client):
    campaign = client.post("/api/merchant/campaigns", json={
        "name": "Draft one", "discount_percent": 5}).json()
    assert campaign["status"] == "DRAFT"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def test_metrics_start_at_zero_and_reflect_real_activity(client, session_id):
    before = client.get("/api/merchant/metrics", params={"scope": "live"}).json()
    _complete_purchase(client, session_id)
    after = client.get("/api/merchant/metrics", params={"scope": "live"}).json()

    assert after["paid_orders"] == before["paid_orders"] + 1
    assert after["gmv_paise"] > before["gmv_paise"]
    assert after["aov_paise"] > 0
    assert after["conversion_rate_percent"] > 0
    assert after["label"] == "LIVE TEST-MODE DATA"


def test_synthetic_and_live_metrics_are_kept_separate(client):
    overview = client.get("/api/merchant/overview").json()
    assert overview["live"]["is_synthetic"] is False
    assert overview["synthetic"]["is_synthetic"] is True
    assert overview["synthetic"]["label"] == "SYNTHETIC / DEMO DATA"
    assert "TEST MODE" in overview["disclaimer"]


def test_failed_payments_are_counted(client, session_id):
    before = client.get("/api/merchant/metrics", params={"scope": "live"}).json()
    _complete_purchase(client, session_id, sku="ACC-STD-013", outcome="failure")
    after = client.get("/api/merchant/metrics", params={"scope": "live"}).json()
    assert after["failed_payments"] > before["failed_payments"]
    assert after["payment_failure_rate_percent"] > 0


def test_upsell_revenue_is_attributed(client, session_id):
    client.post("/api/buyer/chat", json={
        "session_id": session_id,
        "message": "I need a laptop for programming under ₹80,000"})
    client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "Add the best one"})
    client.post("/api/buyer/chat", json={"session_id": session_id, "message": "Yes"})

    quote = client.post("/api/payments/prepare", json={"session_id": session_id}).json()
    payment = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": True}).json()
    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": "success"}).json()
    client.post("/api/payments/verify", json={
        "razorpay_order_id": attempt["provider_order_id"],
        "razorpay_payment_id": attempt["provider_payment_id"],
        "razorpay_signature": attempt["signature"]})

    metrics = client.get("/api/merchant/metrics", params={"scope": "live"}).json()
    attributed = (metrics["upsell_revenue_paise"] + metrics["cross_sell_revenue_paise"])
    assert attributed > 0
    assert metrics["addon_acceptance_rate_percent"] > 0


def test_live_activity_lists_sessions(client, session_id):
    _complete_purchase(client, session_id)
    sessions = client.get("/api/merchant/activity").json()["sessions"]
    entry = next(s for s in sessions if s["session_id"] == session_id)
    assert entry["order_status"] == "PAID"
    assert entry["total_paise"] > 0


def test_no_metric_is_hardcoded(client):
    """A fresh install reports zeros, not aspirational numbers."""
    from app.db import SessionLocal
    from app.models import AuditEvent, BuyerSession, Order

    db = SessionLocal()
    try:
        db.query(AuditEvent).delete()
        db.query(Order).delete()
        db.query(BuyerSession).delete()
        db.commit()
    finally:
        db.close()

    metrics = client.get("/api/merchant/metrics", params={"scope": "all"}).json()
    assert metrics["sessions"] == 0
    assert metrics["gmv_paise"] == 0
    assert metrics["conversion_rate_percent"] == 0.0
    assert metrics["aov_paise"] == 0


# ---------------------------------------------------------------------------
# Growth experiment
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_growth_simulation_produces_real_measured_numbers(client):
    result = client.post("/api/merchant/simulate", json={
        "sessions_per_arm": 12, "seed": 4242}).json()

    assert result["baseline"]["sessions"] == 12
    assert result["ai_assisted"]["sessions"] == 12
    assert result["baseline"]["is_synthetic"] is True
    assert "SYNTHETIC" in result["label_warning"]

    # The numbers must be internally consistent, not decorative.
    for arm in ("baseline", "ai_assisted"):
        m = result[arm]
        assert m["paid_orders"] <= m["orders_created"]
        assert m["converting_sessions"] <= m["sessions"]
        if m["paid_orders"]:
            assert m["aov_paise"] == round(m["gmv_paise"] / m["paid_orders"])
        else:
            assert m["gmv_paise"] == 0

    # The experimental variable actually did something.
    assert result["ai_assisted"]["upsell_suggested_sessions"] > 0
    assert result["baseline"]["upsell_revenue_paise"] == 0

    comparison = result["comparison"]
    assert "aov_paise" in comparison
    assert "statistical significance" in comparison["statistical_note"]
    assert "seeded behavioural assumptions" in result["assumptions"]["note"]


@pytest.mark.slow
def test_simulation_is_reproducible_for_a_given_seed(client):
    a = client.post("/api/merchant/simulate",
                    json={"sessions_per_arm": 8, "seed": 99}).json()
    b = client.post("/api/merchant/simulate",
                    json={"sessions_per_arm": 8, "seed": 99}).json()
    assert a["baseline"]["paid_orders"] == b["baseline"]["paid_orders"]
    assert a["ai_assisted"]["gmv_paise"] == b["ai_assisted"]["gmv_paise"]


@pytest.mark.slow
def test_simulation_does_not_pollute_live_metrics(client):
    before = client.get("/api/merchant/metrics", params={"scope": "live"}).json()
    client.post("/api/merchant/simulate", json={"sessions_per_arm": 6, "seed": 7})
    after = client.get("/api/merchant/metrics", params={"scope": "live"}).json()

    assert after["sessions"] == before["sessions"]
    assert after["gmv_paise"] == before["gmv_paise"]
    assert client.get("/api/merchant/metrics",
                      params={"scope": "synthetic"}).json()["sessions"] > 0


@pytest.mark.slow
def test_simulated_orders_are_flagged_synthetic(client):
    client.post("/api/merchant/simulate", json={"sessions_per_arm": 5, "seed": 11})
    sessions = client.get("/api/merchant/activity", params={"limit": 50}).json()["sessions"]
    simulated = [s for s in sessions if s["channel"] == "simulation"]
    assert simulated
    assert all(s["is_synthetic"] for s in simulated)


@pytest.mark.slow
def test_experiments_are_listed(client):
    client.post("/api/merchant/simulate", json={"sessions_per_arm": 4, "seed": 5})
    experiments = client.get("/api/merchant/experiments").json()["experiments"]
    assert experiments
    assert experiments[0]["is_synthetic"] is True
    assert experiments[0]["results"]["comparison"]
