"""Catalog CRUD, search, and cart mechanics."""
from __future__ import annotations

import pytest

from .conftest import product_id


# ---------------------------------------------------------------------------
# Catalog CRUD
# ---------------------------------------------------------------------------
def test_product_lifecycle(client):
    created = client.post("/api/merchant/products", json={
        "sku": "TEST-CRUD-001", "name": "Test Widget",
        "description": "A widget for testing.", "category": "Accessories",
        "brand": "TestLab", "price_paise": 149_900, "inventory": 5,
        "tags": ["test"], "attributes": {"colour": "black"}})
    assert created.status_code == 201, created.text
    product = created.json()
    assert product["price_display"] == "₹1,499.00"

    updated = client.put(f"/api/merchant/products/{product['id']}", json={
        "price_paise": 199_900, "inventory": 12}).json()
    assert updated["price_paise"] == 199_900
    assert updated["inventory"] == 12

    deleted = client.delete(f"/api/merchant/products/{product['id']}").json()
    assert deleted["active"] is False

    # Soft delete: still fetchable, but no longer purchasable.
    assert client.get(f"/api/agent/products/{product['id']}").json()["active"] is False


def test_duplicate_sku_is_rejected(client):
    body = {"sku": "LAP-DEV-001", "name": "Clash", "price_paise": 1000}
    assert client.post("/api/merchant/products", json=body).status_code == 409


def test_negative_price_is_rejected_by_the_schema(client):
    resp = client.post("/api/merchant/products", json={
        "sku": "TEST-NEG-001", "name": "Negative", "price_paise": -5000})
    assert resp.status_code == 422


def test_unknown_product_update_is_404(client):
    assert client.put("/api/merchant/products/prd_nope",
                      json={"inventory": 1}).status_code == 404


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("query,expected_category", [
    ("laptop for programming", "Laptops"),
    ("phone", "Smartphones"),
    ("camera for travel", "Cameras"),
    ("headphones", "Audio"),
    ("monitor", "Monitors"),
])
def test_search_finds_the_right_category(client, query, expected_category):
    results = client.post("/api/agent/search",
                          json={"query": query, "limit": 5}).json()["results"]
    assert results
    assert any(r["category"] == expected_category for r in results[:3]), \
        f"{query!r} returned {[r['category'] for r in results[:3]]}"


def test_search_respects_a_price_ceiling(client):
    results = client.post("/api/agent/search", json={
        "query": "laptop", "max_price_paise": 5_000_000, "limit": 20}).json()["results"]
    assert results
    assert all(r["price_paise"] <= 5_000_000 for r in results)


def test_search_excludes_out_of_stock_by_default(client, db):
    from app.models import Product

    pid = product_id(client, "LAP-GAM-005")
    db.get(Product, pid).inventory = 0
    db.commit()

    results = client.post("/api/agent/search", json={
        "query": "Vortex Raptor gaming", "limit": 20}).json()["results"]
    assert all(r["id"] != pid for r in results)


def test_empty_query_still_returns_a_ranked_list(client):
    results = client.post("/api/agent/search", json={"query": "", "limit": 5}).json()
    assert results["count"] > 0


def test_categories_and_brands_are_derived_from_the_catalog(client):
    catalog = client.get("/api/agent/catalog").json()
    names = {c["category"] for c in catalog["categories"]}
    assert {"Laptops", "Smartphones", "Cameras", "Audio", "Accessories"} <= names
    assert "Kestrel" in catalog["brands"]
    for entry in catalog["categories"]:
        assert entry["product_count"] > 0
        assert entry["min_price_paise"] <= entry["max_price_paise"]


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------
def test_cart_add_increment_decrement_remove_clear(client, session_id):
    pid = product_id(client, "ACC-MOU-011")

    cart = client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1}).json()
    assert cart["item_count"] == 1
    unit = cart["lines"][0]["unit_price_paise"]

    cart = client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 2}).json()
    assert cart["item_count"] == 3
    assert cart["subtotal_paise"] == unit * 3

    cart = client.post("/api/buyer/cart/quantity", json={
        "session_id": session_id, "product_id": pid, "quantity": 2}).json()
    assert cart["item_count"] == 2
    assert cart["subtotal_paise"] == unit * 2

    cart = client.post("/api/buyer/cart/quantity", json={
        "session_id": session_id, "product_id": pid, "quantity": 0}).json()
    assert cart["item_count"] == 0

    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1})
    cart = client.post("/api/buyer/cart/remove", json={
        "session_id": session_id, "product_id": pid}).json()
    assert cart["item_count"] == 0

    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 2})
    cart = client.post(f"/api/buyer/cart/clear/{session_id}").json()
    assert cart["item_count"] == 0
    assert cart["total_paise"] == 0


def test_cart_persists_across_requests(client, session_id):
    pid = product_id(client, "AUD-BUD-032")
    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1})

    for _ in range(3):
        cart = client.get(f"/api/buyer/cart/{session_id}").json()
        assert cart["item_count"] == 1


def test_empty_cart_has_the_same_shape_as_a_full_one(client, session_id):
    empty = client.get(f"/api/buyer/cart/{session_id}").json()
    pid = product_id(client, "ACC-MOU-011")
    full = client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1}).json()

    money_fields = {"subtotal_paise", "discount_paise", "tax_paise", "total_paise",
                    "item_count", "currency", "total_display", "fingerprint"}
    assert money_fields <= set(empty)
    assert money_fields <= set(full)
    assert empty["total_paise"] == 0


def test_adding_an_unknown_product_is_refused(client, session_id):
    resp = client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": "prd_nonexistent", "quantity": 1})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "product_not_found"


def test_adding_a_deactivated_product_is_refused(client, session_id):
    pid = product_id(client, "WEA-BND-052")
    client.delete(f"/api/merchant/products/{pid}")
    resp = client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] in ("product_inactive", "insufficient_inventory")


def test_a_catalog_price_change_reprices_an_open_cart(client, session_id, db):
    """An open cart always reflects the live price — it cannot undercharge."""
    from app.models import Product

    pid = product_id(client, "ACC-HUB-014")
    cart = client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1}).json()
    original = cart["subtotal_paise"]

    db.get(Product, pid).price_paise = original + 50_000
    db.commit()

    cart = client.get(f"/api/buyer/cart/{session_id}").json()
    assert cart["subtotal_paise"] == original + 50_000


def test_a_paid_order_is_immutable(client, session_id):
    pid = product_id(client, "ACC-STD-013")
    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1})
    quote = client.post("/api/payments/prepare", json={"session_id": session_id}).json()
    payment = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": True}).json()
    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": "success"}).json()
    client.post("/api/payments/verify", json={
        "razorpay_order_id": attempt["provider_order_id"],
        "razorpay_payment_id": attempt["provider_payment_id"],
        "razorpay_signature": attempt["signature"]})

    # A new cart opens for the session; the paid order is untouched.
    cart = client.get(f"/api/buyer/cart/{session_id}").json()
    assert cart["order_id"] != quote["order_id"] or cart["item_count"] == 0
    assert client.get(
        f"/api/payments/order/{quote['order_id']}").json()["status"] == "PAID"


# ---------------------------------------------------------------------------
# Upsell bounds
# ---------------------------------------------------------------------------
def test_upsells_come_only_from_curated_relationships(client, session_id):
    pid = product_id(client, "CAM-MIR-024")
    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1})

    result = client.get(f"/api/agent/upsell/{session_id}").json()
    anchor = client.get(f"/api/agent/products/{pid}").json()
    allowed = set(anchor["frequently_bought_together"]) | \
        set(anchor["compatible_products"]) | set(anchor["related_products"])

    assert result["suggestions"]
    for suggestion in result["suggestions"]:
        assert suggestion["product_id"] in allowed, "invented compatibility"


def test_upsell_respects_its_bounds(client, session_id):
    pid = product_id(client, "CAM-MIR-024")
    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1})
    result = client.get(f"/api/agent/upsell/{session_id}").json()

    bounds = result["bounds"]
    assert len(result["suggestions"]) <= bounds["max_suggestions"]
    total_addons = sum(s["incremental_paise"] for s in result["suggestions"])
    assert total_addons <= bounds["max_total_upsell_paise"]
    for suggestion in result["suggestions"]:
        assert suggestion["incremental_paise"] <= bounds["max_single_upsell_paise"]
        assert suggestion["new_total_paise"] <= bounds["max_order_value_paise"]


def test_upsell_can_be_switched_off_by_the_merchant(client, session_id):
    client.put("/api/merchant/settings", json={
        "upsell_enabled": False, "cross_sell_enabled": False})
    try:
        pid = product_id(client, "CAM-MIR-024")
        client.post("/api/buyer/cart/add", json={
            "session_id": session_id, "product_id": pid, "quantity": 1})
        result = client.get(f"/api/agent/upsell/{session_id}").json()
        assert result["suggestions"] == []
    finally:
        client.put("/api/merchant/settings", json={
            "upsell_enabled": True, "cross_sell_enabled": True})


def test_upsell_rejections_are_explained(client, session_id):
    """Anything filtered out says why — that is the 'bounded' part being visible."""
    pid = product_id(client, "LAP-GAM-005")
    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1})
    result = client.get(f"/api/agent/upsell/{session_id}").json()
    for entry in result["rejected"]:
        assert entry["reason"]
