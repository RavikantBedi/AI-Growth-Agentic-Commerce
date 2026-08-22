"""Graceful failure handling.

Four scenarios, each asserting the same core invariant: **an order is never
marked PAID without a successful server-side verification**, and the buyer is
always told the truth about what happened.
"""
from __future__ import annotations

import pytest

from app.ai.provider import LLMUnavailable
from app.payments import get_payment_provider

from .conftest import product_id


def _cart_and_quote(client, session_id, sku="LAP-DEV-001", quantity=1):
    pid = product_id(client, sku)
    resp = client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": quantity})
    assert resp.status_code == 200, resp.text
    quote = client.post("/api/payments/prepare", json={"session_id": session_id})
    assert quote.status_code == 200, quote.text
    return pid, quote.json()


def _create_payment(client, quote):
    resp = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": True})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Failure 1 — the payment itself fails
# ---------------------------------------------------------------------------
def test_failure_1_declined_payment_never_marks_order_paid(client, session_id):
    pid, quote = _cart_and_quote(client, session_id)
    stock_before = client.get(f"/api/agent/products/{pid}").json()["inventory"]
    payment = _create_payment(client, quote)

    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": "failure"}).json()

    verified = client.post("/api/payments/verify", json={
        "razorpay_order_id": attempt["provider_order_id"],
        "razorpay_payment_id": attempt["provider_payment_id"],
        "razorpay_signature": attempt["signature"]})
    assert verified.status_code == 200
    result = verified.json()

    assert result["paid"] is False
    assert result["verified"] is False
    assert result["payment_status"] == "FAILED"
    assert "NOT been marked as paid" in result["user_message"]
    assert result["retry_available"] is True

    order = client.get(f"/api/payments/order/{quote['order_id']}").json()
    assert order["status"] == "PAYMENT_FAILED"
    assert order["payment_id"] is None

    # Stock must not move on a failed payment.
    assert client.get(f"/api/agent/products/{pid}").json()["inventory"] == stock_before

    # And the failure is on the record.
    story = client.get(f"/api/audit/story/{quote['order_id']}").json()
    assert story["narrative"]["was_it_verified"]["order_is_paid"] is False
    assert "NOT marked paid" in story["narrative"]["was_it_verified"]["statement"]
    assert "PAYMENT_FAILED" in {e["action"] for e in story["timeline"]}


def test_failed_order_can_be_retried(client, session_id):
    _, quote = _cart_and_quote(client, session_id)
    payment = _create_payment(client, quote)
    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": "failure"}).json()
    client.post("/api/payments/verify", json={
        "razorpay_order_id": attempt["provider_order_id"],
        "razorpay_payment_id": attempt["provider_payment_id"],
        "razorpay_signature": attempt["signature"]})

    # A fresh quote must be obtainable, and the retry must succeed cleanly.
    retry_quote = client.post("/api/payments/prepare", json={"session_id": session_id})
    assert retry_quote.status_code == 200, retry_quote.text
    retry_payment = _create_payment(client, retry_quote.json())
    retry_attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": retry_payment["provider_order_id"],
        "outcome": "success"}).json()
    result = client.post("/api/payments/verify", json={
        "razorpay_order_id": retry_attempt["provider_order_id"],
        "razorpay_payment_id": retry_attempt["provider_payment_id"],
        "razorpay_signature": retry_attempt["signature"]}).json()

    assert result["paid"] is True
    assert client.get(
        f"/api/payments/order/{quote['order_id']}").json()["status"] == "PAID"


# ---------------------------------------------------------------------------
# Failure 2 — verification fails / the provider is unavailable
# ---------------------------------------------------------------------------
def test_failure_2_tampered_signature_is_verification_failed(client, session_id):
    _, quote = _cart_and_quote(client, session_id)
    payment = _create_payment(client, quote)

    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"],
        "outcome": "tampered_signature"}).json()

    result = client.post("/api/payments/verify", json={
        "razorpay_order_id": attempt["provider_order_id"],
        "razorpay_payment_id": attempt["provider_payment_id"],
        "razorpay_signature": attempt["signature"]}).json()

    assert result["paid"] is False
    assert result["payment_status"] == "VERIFICATION_FAILED"
    assert result["verification_status"] == "FAILED"
    assert client.get(
        f"/api/payments/order/{quote['order_id']}").json()["status"] == "PAYMENT_FAILED"

    story = client.get(f"/api/audit/story/{quote['order_id']}").json()
    assert "PAYMENT_VERIFICATION_FAILED" in {e["action"] for e in story["timeline"]}


def test_forged_client_success_claim_is_rejected(client, session_id):
    """A browser that simply POSTs a made-up payment id proves nothing."""
    _, quote = _cart_and_quote(client, session_id)
    payment = _create_payment(client, quote)

    result = client.post("/api/payments/verify", json={
        "razorpay_order_id": payment["provider_order_id"],
        "razorpay_payment_id": "pay_totally_made_up_id",
        "razorpay_signature": "a" * 64}).json()

    assert result["paid"] is False
    assert result["payment_status"] == "VERIFICATION_FAILED"


def test_failure_2b_provider_outage_leaves_order_unpaid(client, session_id):
    _, quote = _cart_and_quote(client, session_id)
    client.post("/api/merchant/failure-injection", json={"outage": True})

    refused = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": True})
    assert refused.status_code == 503
    detail = refused.json()["detail"]
    assert detail["error"] == "provider_unavailable"
    assert detail["detail"]["retryable"] is True

    order = client.get(f"/api/payments/order/{quote['order_id']}").json()
    assert order["status"] != "PAID"

    # Recovery: with the provider back, the same quote still works.
    client.post("/api/merchant/failure-injection", json={"outage": False})
    payment = _create_payment(client, quote)
    assert payment["provider_order_id"]


def test_forced_verification_failure_then_reconciliation(client, session_id):
    """The recovery path: a real captured payment is found by reconciliation."""
    _, quote = _cart_and_quote(client, session_id)
    payment = _create_payment(client, quote)
    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": "success"}).json()

    client.post("/api/merchant/failure-injection", json={"verification_failure": True})
    result = client.post("/api/payments/verify", json={
        "razorpay_order_id": attempt["provider_order_id"],
        "razorpay_payment_id": attempt["provider_payment_id"],
        "razorpay_signature": attempt["signature"]}).json()

    assert result["paid"] is False
    assert result["payment_status"] == "VERIFICATION_FAILED"
    assert client.get(
        f"/api/payments/order/{quote['order_id']}").json()["status"] == "PAYMENT_FAILED"

    # Reconcile once verification is working: the provider is the source of truth.
    client.post("/api/merchant/failure-injection", json={"verification_failure": False})
    reconciled = client.post(f"/api/payments/reconcile/{quote['order_id']}").json()

    assert reconciled["paid"] is True
    assert reconciled["verified"] is True
    assert client.get(
        f"/api/payments/order/{quote['order_id']}").json()["status"] == "PAID"


# ---------------------------------------------------------------------------
# Failure 3 — inventory disappears before checkout
# ---------------------------------------------------------------------------
def test_failure_3_stock_gone_before_payment_blocks_checkout(client, session_id, db):
    from app.models import Product

    pid, quote = _cart_and_quote(client, session_id, sku="ACC-STD-013", quantity=2)

    # Someone else buys the remaining stock between quote and confirmation.
    product = db.get(Product, pid)
    product.inventory = 1
    db.commit()

    refused = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": True})
    assert refused.status_code == 400
    detail = refused.json()["detail"]
    assert detail["error"] == "policy_violation"
    assert "Insufficient stock" in detail["message"]

    order = client.get(f"/api/payments/order/{quote['order_id']}").json()
    assert order["status"] != "PAID"


def test_adding_more_than_stock_is_refused_with_a_clear_message(client, session_id, db):
    from app.models import Product

    pid = product_id(client, "CAM-MIR-030")
    product = db.get(Product, pid)
    product.inventory = 2
    db.commit()

    resp = client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 3})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["error"] == "insufficient_inventory"
    assert "left in stock" in detail["message"]
    assert detail["detail"]["available"] == 2


# ---------------------------------------------------------------------------
# Failure 4 — the LLM is unavailable
# ---------------------------------------------------------------------------
def test_failure_4_commerce_works_when_the_llm_is_down(client, session_id, monkeypatch):
    """With every model unreachable, search, cart and checkout still work."""
    import app.ai.agent as agent_module

    class DeadProvider:
        name, model, deterministic = "ollama", "llama3.2", False

        def complete(self, **kwargs):
            raise LLMUnavailable("Simulated: Ollama is not running.")

        def describe(self):
            return {"provider": self.name, "model": self.model, "deterministic": False}

    monkeypatch.setattr(agent_module, "get_llm_provider", lambda: DeadProvider())

    chat = client.post("/api/buyer/chat", json={
        "session_id": session_id,
        "message": "I need a laptop for programming under ₹80,000"})
    assert chat.status_code == 200, chat.text
    body = chat.json()

    assert body["ai"]["degraded"] is True
    assert "Ollama is not running" in body["ai"]["degraded_reason"]
    assert body["recommendations"], "deterministic ranking must still return products"
    assert "deterministic catalog engine" in body["message"]

    # And a full purchase still completes with no model at all.
    client.post("/api/buyer/chat", json={"session_id": session_id,
                                         "message": "Add the best one"})
    quote = client.post("/api/payments/prepare", json={"session_id": session_id})
    assert quote.status_code == 200, quote.text
    payment = _create_payment(client, quote.json())
    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": "success"}).json()
    result = client.post("/api/payments/verify", json={
        "razorpay_order_id": attempt["provider_order_id"],
        "razorpay_payment_id": attempt["provider_payment_id"],
        "razorpay_signature": attempt["signature"]}).json()
    assert result["paid"] is True


def test_malformed_model_output_is_rejected_not_executed(client, session_id, monkeypatch):
    import app.ai.agent as agent_module
    from app.ai.provider import LLMResponse

    class GarbageProvider:
        name, model, deterministic = "ollama", "llama3.2", False

        def complete(self, **kwargs):
            return LLMResponse(text="I'm not JSON at all, sorry!", provider=self.name,
                               model=self.model, latency_ms=1.0)

        def describe(self):
            return {"provider": self.name, "model": self.model, "deterministic": False}

    monkeypatch.setattr(agent_module, "get_llm_provider", lambda: GarbageProvider())

    body = client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "I need a phone under ₹30,000"}).json()

    assert body["ai"]["degraded"] is True
    assert body["recommendations"]

    events = client.get("/api/audit/events",
                        params={"session_id": session_id,
                                "action": "AI_RESPONSE_REJECTED"}).json()
    assert events["count"] >= 1
    assert events["events"][0]["decision"] == "REJECTED"


def test_ai_health_reports_degradation_honestly(client):
    status = client.get("/api/merchant/ai").json()["provider"]
    assert status["active"]["provider"] in ("mock", "ollama", "claude")
    assert status["fallback"]["deterministic"] is True
    assert "does not depend on the LLM" in status["note"]


@pytest.mark.parametrize("outcome", ["failure", "tampered_signature"])
def test_no_failure_path_ever_produces_a_paid_order(client, session_id, outcome):
    _, quote = _cart_and_quote(client, session_id)
    payment = _create_payment(client, quote)
    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": outcome}).json()
    client.post("/api/payments/verify", json={
        "razorpay_order_id": attempt["provider_order_id"],
        "razorpay_payment_id": attempt["provider_payment_id"],
        "razorpay_signature": attempt["signature"]})
    assert client.get(
        f"/api/payments/order/{quote['order_id']}").json()["status"] != "PAID"


def test_failure_injection_cannot_fake_success(client):
    """The chaos proxy is additive only — there is no 'force success' switch."""
    provider = get_payment_provider()
    assert set(provider.failure_mode()) == {"outage", "verification_failure"}
    resp = client.post("/api/merchant/failure-injection",
                       json={"outage": False, "verification_failure": False}).json()
    assert "no setting that fakes a successful payment" in resp["note"]
