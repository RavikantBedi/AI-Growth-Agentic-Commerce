"""The full end-to-end journey, exercised as one automated test.

    merchant + catalog -> buyer session -> search -> real catalog retrieval
    -> recommendation -> accept -> bounded upsell -> accept
    -> backend-calculated cart -> policy check -> explicit confirmation
    -> test-mode payment order -> payment -> server verification
    -> order PAID -> audit trail

Nothing here fakes a payment result: the sandbox provider issues a real HMAC
signature and the backend verifies it through the same code path Razorpay uses.
"""
from __future__ import annotations

from .conftest import product_id


def test_full_purchase_journey(client, session_id):
    # -- 1. The merchant is discoverable and machine-readable ------------
    manifest = client.get("/.well-known/agent-commerce.json").json()
    assert manifest["spec"] == "nova.agent-commerce"
    assert manifest["agent_constraints"]["agent_may_create_payment"] is False
    assert manifest["policies"]["requires_user_confirmation"] is True

    catalog = client.get("/api/agent/catalog").json()
    assert catalog["total_products"] > 20
    assert catalog["currency"] == "INR"

    # -- 2. The buyer states a need in natural language -------------------
    chat = client.post("/api/buyer/chat", json={
        "session_id": session_id,
        "message": "I need a laptop for programming under ₹80,000",
    })
    assert chat.status_code == 200, chat.text
    body = chat.json()

    assert body["requirements"]["max_price_paise"] == 8_000_000
    assert body["recommendations"], "expected real catalog recommendations"

    # Every recommendation is a real catalog row, within budget.
    for rec in body["recommendations"]:
        assert rec["price_paise"] <= 8_000_000
        detail = client.get(f"/api/agent/products/{rec['id']}")
        assert detail.status_code == 200
        assert detail.json()["price_paise"] == rec["price_paise"]

    # -- 3. The buyer accepts the recommendation -------------------------
    chat = client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "Add the best one"}).json()
    cart = chat["cart"]
    assert cart["item_count"] == 1
    base_total = cart["total_paise"]
    assert base_total > 0

    # -- 4. Bounded upsell is offered with its exact price impact --------
    upsells = chat["upsells"]
    assert upsells, "expected bounded add-on suggestions"
    for suggestion in upsells:
        assert suggestion["incremental_paise"] > 0
        assert suggestion["new_total_paise"] > cart["subtotal_paise"]
        assert suggestion["reason"]
        # Bound: no single add-on may exceed 35% of the cart subtotal.
        assert suggestion["incremental_paise"] <= chat["upsell_bounds"][
            "max_single_upsell_paise"]

    # -- 5. The buyer accepts the add-ons --------------------------------
    accepted = client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "Yes, add them"}).json()
    cart = accepted["cart"]
    assert cart["item_count"] > 1
    assert cart["total_paise"] > base_total

    # -- 6. The backend, not the client, computes the total --------------
    expected_subtotal = sum(l["unit_price_paise"] * l["quantity"] for l in cart["lines"])
    assert cart["subtotal_paise"] == expected_subtotal
    expected_tax = round((cart["subtotal_paise"] - cart["discount_paise"]) * 0.18)
    assert abs(cart["tax_paise"] - expected_tax) <= 1
    assert cart["total_paise"] == (cart["subtotal_paise"] - cart["discount_paise"]
                                   + cart["tax_paise"] + cart["shipping_paise"])

    # -- 7. Prepare: policy check + explanation, no payment yet ----------
    prepared = client.post("/api/payments/prepare", json={"session_id": session_id})
    assert prepared.status_code == 200, prepared.text
    quote = prepared.json()

    assert quote["requires_confirmation"] is True
    assert quote["policy_result"]["allowed"] is True
    assert len(quote["policy_result"]["checks"]) >= 9
    assert quote["cart"]["total_paise"] == cart["total_paise"]
    assert "You are about to pay" in quote["explanation"]
    assert "TEST-MODE" in quote["explanation"]

    order_id = quote["order_id"]
    assert client.get(f"/api/agent/order/{order_id}").json()["status"] == "CHECKOUT_PENDING"

    # -- 8. Payment without confirmation is refused ----------------------
    refused = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": False})
    assert refused.status_code == 400
    assert refused.json()["detail"]["error"] == "confirmation_required"

    # -- 9. Explicit confirmation creates the provider order -------------
    confirmed = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": True,
        "confirmed_by": "test-buyer"})
    assert confirmed.status_code == 200, confirmed.text
    payment = confirmed.json()

    assert payment["amount_paise"] == cart["total_paise"]
    assert payment["test_mode"] is True
    assert payment["payment_status"] == "CREATED"
    assert client.get(f"/api/agent/order/{order_id}").json()["status"] == "PAYMENT_PENDING"

    # -- 10. The buyer pays, and the server verifies independently -------
    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": "success"})
    assert attempt.status_code == 200, attempt.text
    triple = attempt.json()

    verified = client.post("/api/payments/verify", json={
        "razorpay_order_id": triple["provider_order_id"],
        "razorpay_payment_id": triple["provider_payment_id"],
        "razorpay_signature": triple["signature"]})
    assert verified.status_code == 200, verified.text
    result = verified.json()

    assert result["paid"] is True
    assert result["verified"] is True
    assert result["payment_status"] == "CAPTURED"
    assert result["verification_status"] == "VERIFIED"
    assert result["amount_paise"] == cart["total_paise"]

    # -- 11. The order is PAID and stock was reduced ---------------------
    order = client.get(f"/api/payments/order/{order_id}").json()
    assert order["status"] == "PAID"
    assert order["payment_id"] == triple["provider_payment_id"]
    assert order["transactions"][-1]["verification_status"] == "VERIFIED"

    # -- 12. The audit trail can explain the whole thing -----------------
    story = client.get(f"/api/audit/story/{order_id}").json()
    narrative = story["narrative"]

    assert narrative["what_was_requested"]
    assert narrative["what_is_being_bought"]
    assert narrative["which_policy"]["allowed"] is True
    assert narrative["who_approved"]["actor"] == "test-buyer"
    assert narrative["who_approved"]["decision"] == "ALLOWED"
    assert narrative["which_provider"]["provider_order_id"] == triple["provider_order_id"]
    assert narrative["was_it_verified"]["order_is_paid"] is True
    assert narrative["was_it_verified"]["verification_status"] == "VERIFIED"

    actions = {e["action"] for e in story["timeline"]}
    for required in ("PRODUCT_SEARCHED", "CART_UPDATED", "PRICE_CALCULATED",
                     "POLICY_CHECKED", "PAYMENT_CONFIRMATION_REQUESTED",
                     "PAYMENT_CONFIRMED_BY_USER", "PAYMENT_ORDER_CREATED",
                     "PAYMENT_ATTEMPTED", "PAYMENT_VERIFIED", "ORDER_PAID",
                     "INVENTORY_DECREMENTED"):
        assert required in actions, f"missing audit action {required}"


def test_inventory_decremented_only_after_verified_payment(client, session_id):
    pid = product_id(client, "ACC-STD-013")
    before = client.get(f"/api/agent/products/{pid}").json()["inventory"]

    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 2})
    quote = client.post("/api/payments/prepare", json={"session_id": session_id}).json()
    payment = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": True}).json()

    # A created-but-unpaid order must not have touched stock.
    assert client.get(f"/api/agent/products/{pid}").json()["inventory"] == before

    triple = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": "success"}).json()
    client.post("/api/payments/verify", json={
        "razorpay_order_id": triple["provider_order_id"],
        "razorpay_payment_id": triple["provider_payment_id"],
        "razorpay_signature": triple["signature"]})

    assert client.get(f"/api/agent/products/{pid}").json()["inventory"] == before - 2
