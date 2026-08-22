"""Policy engine, price integrity and money arithmetic."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.money import format_inr, paise_to_rupees, percent_of, rupees_to_paise
from app.domain.policy import (CartLineView, PurchasePolicy, Rule, clamp_discount,
                               evaluate)
from app.domain.pricing import price_cart
from app.domain.states import (IllegalTransition, OrderStatus, PaymentStatus,
                               can_transition_order, transition_order,
                               transition_payment)

from .conftest import product_id


def _line(**overrides) -> CartLineView:
    base = dict(product_id="prd_1", name="Test item", category="Laptops",
                unit_price_paise=100_000, quantity=1, inventory_available=10,
                active=True, catalog_price_paise=100_000)
    base.update(overrides)
    return CartLineView(**base)


DEFAULT = PurchasePolicy()


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rupees,paise", [
    (0, 0), (1, 100), (1250, 125_000), (70_000, 7_000_000),
    (0.01, 1), (0.005, 1), (19.99, 1999), ("1234.56", 123_456),
])
def test_rupee_to_paise_conversion(rupees, paise):
    assert rupees_to_paise(rupees) == paise


def test_paise_to_rupees_is_exact():
    assert paise_to_rupees(7_000_000) == Decimal("70000.00")
    assert paise_to_rupees(1) == Decimal("0.01")


@pytest.mark.parametrize("paise,text", [
    (0, "₹0.00"), (100, "₹1.00"), (125_000, "₹1,250.00"),
    (7_000_000, "₹70,000.00"), (11_610_964, "₹1,16,109.64"),
    (100_000_000, "₹10,00,000.00"),
])
def test_indian_digit_grouping(paise, text):
    assert format_inr(paise) == text


def test_percent_of_rounds_half_up():
    assert percent_of(1000, 18) == 180
    assert percent_of(101, 50) == 51     # 50.5 -> 51
    assert percent_of(0, 18) == 0


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
def test_price_cart_computes_the_documented_order_of_operations():
    cart = price_cart(
        [{"product_id": "a", "unit_price_paise": 7_000_000, "quantity": 1},
         {"product_id": "b", "unit_price_paise": 120_000, "quantity": 2}],
        tax_percent=18, discount_percent=10)

    assert cart.subtotal_paise == 7_240_000
    assert cart.discount_paise == 724_000
    assert cart.taxable_paise == 6_516_000
    assert cart.tax_paise == percent_of(6_516_000, 18)
    assert cart.total_paise == cart.taxable_paise + cart.tax_paise


def test_discount_cap_is_respected():
    cart = price_cart(
        [{"product_id": "a", "unit_price_paise": 1_000_000, "quantity": 1}],
        discount_percent=50, max_discount_paise=100_000)
    assert cart.discount_paise == 100_000
    assert cart.discount_percent == 10.0


def test_discount_can_never_exceed_the_subtotal():
    cart = price_cart(
        [{"product_id": "a", "unit_price_paise": 1000, "quantity": 1}],
        discount_percent=500)
    assert cart.discount_paise == 1000
    assert cart.total_paise == 0


def test_fingerprint_changes_when_anything_monetary_changes():
    items = [{"product_id": "a", "unit_price_paise": 1000, "quantity": 1}]
    base = price_cart(items, tax_percent=18).fingerprint()

    assert price_cart(items, tax_percent=18).fingerprint() == base
    assert price_cart(items, tax_percent=12).fingerprint() != base
    assert price_cart([{**items[0], "quantity": 2}], tax_percent=18).fingerprint() != base
    assert price_cart([{**items[0], "unit_price_paise": 1001}],
                      tax_percent=18).fingerprint() != base


def test_upsell_revenue_is_attributed_by_source():
    cart = price_cart([
        {"product_id": "a", "unit_price_paise": 7_000_000, "quantity": 1,
         "source": "direct"},
        {"product_id": "b", "unit_price_paise": 120_000, "quantity": 1,
         "source": "upsell"},
        {"product_id": "c", "unit_price_paise": 125_000, "quantity": 1,
         "source": "cross_sell"},
    ])
    assert cart.upsell_paise == 120_000
    assert cart.cross_sell_paise == 125_000


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------
def test_policy_passes_a_clean_cart():
    result = evaluate(DEFAULT, [_line()], total_paise=118_000, currency="INR")
    assert result.allowed is True
    assert result.violations == []
    assert "policy checks passed" in result.summary


def test_max_order_value_is_enforced():
    result = evaluate(DEFAULT, [_line(unit_price_paise=20_000_000,
                                      catalog_price_paise=20_000_000)],
                      total_paise=20_000_000, currency="INR")
    assert result.allowed is False
    assert [v.rule for v in result.violations] == [Rule.MAX_ORDER_VALUE]
    assert "exceeds the merchant's maximum order value" in result.summary


def test_wrong_currency_is_rejected():
    result = evaluate(DEFAULT, [_line()], total_paise=100_000, currency="USD")
    assert result.allowed is False
    assert Rule.ALLOWED_CURRENCY in [v.rule for v in result.violations]


def test_insufficient_inventory_is_rejected_with_detail():
    result = evaluate(DEFAULT, [_line(quantity=5, inventory_available=2)],
                      total_paise=500_000, currency="INR")
    assert result.allowed is False
    violation = next(v for v in result.violations if v.rule == Rule.INVENTORY_AVAILABLE)
    assert violation.observed[0]["requested"] == 5
    assert violation.observed[0]["available"] == 2


def test_price_mismatch_against_the_catalog_is_rejected():
    result = evaluate(DEFAULT, [_line(unit_price_paise=1, catalog_price_paise=100_000)],
                      total_paise=1, currency="INR")
    assert result.allowed is False
    assert Rule.PRICE_INTEGRITY in [v.rule for v in result.violations]


def test_inactive_product_is_rejected():
    result = evaluate(DEFAULT, [_line(active=False)], total_paise=100_000, currency="INR")
    assert result.allowed is False
    assert Rule.ACTIVE_PRODUCTS_ONLY in [v.rule for v in result.violations]


def test_allowed_categories_restrict_the_agent_channel():
    policy = PurchasePolicy(allowed_categories=("Accessories",))
    result = evaluate(policy, [_line(category="Laptops")],
                      total_paise=100_000, currency="INR")
    assert result.allowed is False
    assert Rule.ALLOWED_CATEGORIES in [v.rule for v in result.violations]


def test_policy_reports_every_violation_not_just_the_first():
    result = evaluate(DEFAULT,
                      [_line(unit_price_paise=20_000_000, quantity=99,
                             inventory_available=0, active=False,
                             catalog_price_paise=1)],
                      total_paise=20_000_000, currency="USD")
    rules = {v.rule for v in result.violations}
    assert {Rule.MAX_ORDER_VALUE, Rule.MAX_ITEMS, Rule.MAX_QUANTITY_PER_LINE,
            Rule.ALLOWED_CURRENCY, Rule.ACTIVE_PRODUCTS_ONLY,
            Rule.INVENTORY_AVAILABLE, Rule.PRICE_INTEGRITY} <= rules


def test_confirmation_requirement_is_always_reported():
    result = evaluate(DEFAULT, [_line()], total_paise=100_000, currency="INR")
    assert result.requires_confirmation is True
    check = next(c for c in result.checks if c.rule == Rule.REQUIRES_CONFIRMATION)
    assert "Explicit user confirmation is required" in check.message


# ---------------------------------------------------------------------------
# Discount clamping
# ---------------------------------------------------------------------------
def test_discount_within_the_cap_is_accepted():
    effective, clamped, explanation = clamp_discount(15, PurchasePolicy(max_discount_percent=20))
    assert (effective, clamped) == (15, False)
    assert "within the" in explanation


def test_discount_above_the_cap_is_rejected_and_clamped():
    effective, clamped, explanation = clamp_discount(35, PurchasePolicy(max_discount_percent=20))
    assert effective == 20
    assert clamped is True
    assert explanation.startswith("REJECTED")
    assert "35.00% exceeds" in explanation


def test_negative_discount_is_clamped_to_zero():
    effective, clamped, _ = clamp_discount(-10, DEFAULT)
    assert (effective, clamped) == (0.0, True)


# ---------------------------------------------------------------------------
# State machines
# ---------------------------------------------------------------------------
def test_the_happy_path_transitions_are_legal():
    status = OrderStatus.CART
    for nxt in (OrderStatus.CHECKOUT_PENDING, OrderStatus.PAYMENT_PENDING,
                OrderStatus.PAID):
        status = transition_order(status, nxt)
    assert status is OrderStatus.PAID


@pytest.mark.parametrize("current,target", [
    (OrderStatus.CART, OrderStatus.PAID),
    (OrderStatus.CHECKOUT_PENDING, OrderStatus.PAID),
    (OrderStatus.DRAFT, OrderStatus.PAID),
    (OrderStatus.PAYMENT_FAILED, OrderStatus.PAID),
    (OrderStatus.CANCELLED, OrderStatus.PAID),
    (OrderStatus.PAID, OrderStatus.CART),
])
def test_no_shortcut_to_paid(current, target):
    assert not can_transition_order(current, target)
    with pytest.raises(IllegalTransition):
        transition_order(current, target)


def test_payment_cannot_go_from_failed_to_captured():
    with pytest.raises(IllegalTransition):
        transition_payment(PaymentStatus.FAILED, PaymentStatus.CAPTURED)


def test_repeated_verification_of_the_same_state_is_a_no_op():
    assert transition_payment(PaymentStatus.CAPTURED, PaymentStatus.CAPTURED) \
        is PaymentStatus.CAPTURED


# ---------------------------------------------------------------------------
# Guardrails through the API
# ---------------------------------------------------------------------------
def test_merchant_cannot_raise_limits_above_the_deployment_ceiling(client):
    resp = client.put("/api/merchant/settings", json={
        "max_order_value_paise": 999_999_999, "max_discount_percent": 90,
        "confirmation_required": False})
    assert resp.status_code == 200
    body = resp.json()

    assert body["settings"]["max_order_value_paise"] == 10_000_000
    assert body["settings"]["max_discount_percent"] == 20
    assert body["settings"]["confirmation_required"] is True
    assert len(body["clamped"]) == 3
    assert any("MAX_ORDER_VALUE" in note for note in body["clamped"])

    # Restore for other tests in the session.
    client.put("/api/merchant/settings", json={
        "max_order_value_paise": 10_000_000, "max_discount_percent": 20})


def test_over_limit_order_is_blocked_at_checkout(client, session_id):
    """Two ₹98,000 gaming laptops exceed the ₹1,00,000 cap once taxed."""
    pid = product_id(client, "LAP-GAM-005")
    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 2})

    resp = client.post("/api/payments/prepare", json={"session_id": session_id})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["error"] == "policy_violation"
    assert "maximum order value" in detail["message"]

    violations = detail["detail"]["policy_result"]["violations"]
    assert [v["rule"] for v in violations] == [Rule.MAX_ORDER_VALUE]


def test_policy_rejection_is_audited(client, session_id):
    pid = product_id(client, "LAP-GAM-005")
    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 2})
    client.post("/api/payments/prepare", json={"session_id": session_id})

    events = client.get("/api/audit/events", params={
        "session_id": session_id, "action": "POLICY_CHECKED",
        "decision": "REJECTED"}).json()
    assert events["count"] >= 1
    assert events["events"][0]["policy_result"]["allowed"] is False
