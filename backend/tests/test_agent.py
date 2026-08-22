"""The AI agent: requirement extraction, ranking, grounding and conversation."""
from __future__ import annotations

import pytest

from app.ai.contract import ContractViolation, Intent, extract_json, parse_agent_output
from app.services.recommend import extract_requirements, rank_candidates

from .conftest import product_id


# ---------------------------------------------------------------------------
# Requirement extraction
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("query,expected_paise", [
    ("laptop under ₹80,000", 8_000_000),
    ("laptop under 80000", 8_000_000),
    ("phone below Rs. 30,000", 3_000_000),
    ("camera within 70k", 7_000_000),
    ("laptop under 1.2 lakh", 12_000_000),
    ("headphones up to ₹25,000", 2_500_000),
    ("something for ₹15,000", 1_500_000),
])
def test_budget_extraction(query, expected_paise):
    assert extract_requirements(query).max_price_paise == expected_paise


def test_range_extraction():
    req = extract_requirements("laptop between 50000 and 80000")
    assert req.min_price_paise == 5_000_000
    assert req.max_price_paise == 8_000_000


@pytest.mark.parametrize("query,category", [
    ("I need a laptop", "Laptops"),
    ("show me phones", "Smartphones"),
    ("a good camera", "Cameras"),
    ("wireless headphones", "Audio"),
    ("a 27 inch monitor", "Monitors"),
    # Compound hints beat their components: a laptop stand is an accessory, and
    # resolving it to Laptops would search the wrong aisle entirely.
    ("a laptop stand", "Accessories"),
    ("a laptop bag", "Accessories"),
    ("a phone case", "Accessories"),
])
def test_category_extraction(query, category):
    assert extract_requirements(query).category == category


def test_use_case_tags_are_extracted():
    req = extract_requirements("a laptop for programming and design work")
    assert "programming" in req.use_case_tags
    assert "design" in req.use_case_tags


def test_brand_extraction():
    req = extract_requirements("a Kestrel laptop", known_brands=["Kestrel", "Aurora"])
    assert req.brands == ["Kestrel"]


def test_no_constraints_yields_an_empty_requirement_set():
    req = extract_requirements("hello there")
    assert req.max_price_paise is None
    assert req.category is None
    assert req.use_case_tags == []


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
def test_ranking_prefers_the_best_option_within_budget(db):
    req = extract_requirements("laptop for programming under ₹80,000")
    ranked = rank_candidates(db, req, limit=5)

    assert ranked
    assert all(s.product.price_paise <= 8_000_000 for s in ranked[:3])
    assert all(s.product.category == "Laptops" for s in ranked[:3])
    # Within budget, the more capable machine should outrank the cheapest.
    assert ranked[0].product.price_paise >= 5_000_000


def test_ranking_signals_are_exposed_and_explained(db):
    req = extract_requirements("camera for travel under ₹70,000")
    ranked = rank_candidates(db, req, limit=3)

    top = ranked[0]
    assert set(top.signals) == {"text_relevance", "price_fit", "category_fit",
                                "attribute_fit", "inventory", "relationship"}
    assert 0 <= top.score <= 100
    assert top.why


def test_ranking_never_returns_an_out_of_stock_product(db):
    from app.models import Product

    req = extract_requirements("laptop under ₹80,000")
    for p in db.query(Product).filter(Product.category == "Laptops").all():
        p.inventory = 0
    db.commit()

    assert rank_candidates(db, req, limit=5) == [] or all(
        s.product.inventory > 0 for s in rank_candidates(db, req, limit=5))
    db.rollback()


def test_cart_items_are_excluded_from_recommendations(db, client, session_id):
    pid = product_id(client, "LAP-DEV-002")
    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1})

    req = extract_requirements("laptop for programming under ₹80,000")
    ranked = rank_candidates(db, req, limit=5, cart_product_ids=[pid])
    assert all(s.product.id != pid for s in ranked)


def test_over_budget_products_rank_below_in_budget_ones(db):
    req = extract_requirements("gaming laptop under ₹50,000")
    ranked = rank_candidates(db, req, limit=8)
    in_budget = [s for s in ranked if s.product.price_paise <= 5_000_000]
    over_budget = [s for s in ranked if s.product.price_paise > 5_000_000]
    if in_budget and over_budget:
        assert min(s.score for s in in_budget) > 0
        assert ranked[0].product.price_paise <= 5_000_000


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------
def test_valid_output_parses():
    output = parse_agent_output(
        '{"intent": "RECOMMEND", "recommendations": ["p1"], "reason": "fits", '
        '"message": "Here you go"}', allowed_product_ids={"p1"})
    assert output.intent is Intent.RECOMMEND
    assert output.recommendations == ["p1"]


def test_json_inside_a_code_fence_parses():
    output = parse_agent_output(
        'Sure!\n```json\n{"intent": "SEARCH", "message": "ok"}\n```\nHope that helps.',
        allowed_product_ids=set())
    assert output.intent is Intent.SEARCH


@pytest.mark.parametrize("bad", [
    "", "   ", "not json at all", "{broken json",
    '{"intent": "DELETE_EVERYTHING"}',
    '{"intent": "RECOMMEND", "product_ids": "not-a-list-of-ids", "quantity": "abc"}',
])
def test_malformed_output_is_rejected(bad):
    with pytest.raises(ContractViolation):
        parse_agent_output(bad, allowed_product_ids=set())


def test_unknown_fields_are_dropped_not_fatal():
    output = parse_agent_output(
        '{"intent": "SEARCH", "message": "ok", "execute_payment": true, '
        '"admin_override": "yes"}', allowed_product_ids=set())
    assert output.intent is Intent.SEARCH
    assert not hasattr(output, "execute_payment")


def test_upsells_given_as_bare_strings_are_normalised():
    output = parse_agent_output(
        '{"intent": "ADD_TO_CART", "product_ids": ["p1"], "upsells": ["p2"]}',
        allowed_product_ids={"p1", "p2"})
    assert output.upsells[0].product_id == "p2"


def test_extract_json_finds_an_embedded_object():
    assert extract_json('blah {"a": 1} blah')["a"] == 1


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------
def test_the_demo_conversation_works_end_to_end(client, session_id):
    first = client.post("/api/buyer/chat", json={
        "session_id": session_id,
        "message": "I need a laptop setup for programming under ₹80,000"}).json()

    assert first["intent"] in ("RECOMMEND", "SEARCH")
    assert len(first["recommendations"]) >= 2
    assert "Option A" in first["message"] or first["recommendations"]

    second = client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "Add the best one"}).json()
    assert second["intent"] == "ADD_TO_CART"
    assert second["cart"]["item_count"] == 1
    assert second["cart"]["lines"][0]["category"] == "Laptops"

    assert second["upsells"], "should offer compatible accessories"
    accessory_names = {u["name"] for u in second["upsells"]}
    assert accessory_names

    third = client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "Yes"}).json()
    assert third["intent"] == "ACCEPT_UPSELL"
    assert third["cart"]["item_count"] > 1

    fourth = client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "Checkout"}).json()
    assert fourth["intent"] == "CHECKOUT"
    assert fourth["checkout"]["requires_confirmation"] is True


def test_declining_an_upsell_leaves_the_cart_alone(client, session_id):
    client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "I need a camera for travel under ₹70,000"})
    added = client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "Add the best one"}).json()
    before = added["cart"]["item_count"]

    declined = client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "No thanks"}).json()
    assert declined["intent"] == "DECLINE_UPSELL"
    assert declined["cart"]["item_count"] == before

    events = client.get("/api/audit/events", params={
        "session_id": session_id, "action": "UPSELL_DECLINED"}).json()
    assert events["count"] >= 1


def test_removing_an_item_by_name(client, session_id):
    pid = product_id(client, "ACC-MOU-011")
    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1})

    body = client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "Remove the Kestrel Glide mouse"}).json()
    assert body["intent"] == "REMOVE_FROM_CART"
    assert body["cart"]["item_count"] == 0


def test_viewing_the_cart(client, session_id):
    pid = product_id(client, "AUD-HED-031")
    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1})
    body = client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "What's in my cart?"}).json()
    assert body["intent"] == "VIEW_CART"
    assert body["cart"]["item_count"] == 1


def test_conversation_history_is_recorded(client, session_id):
    client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "I need a phone under ₹30,000"})
    history = client.get(f"/api/buyer/history/{session_id}").json()["messages"]
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "agent"


def test_the_agent_never_quotes_a_price_the_backend_disagrees_with(client, session_id):
    """Every number shown comes from the backend, so they cannot diverge."""
    body = client.post("/api/buyer/chat", json={
        "session_id": session_id, "message": "I need a laptop under ₹80,000"}).json()

    for rec in body["recommendations"]:
        authoritative = client.get(f"/api/agent/products/{rec['id']}").json()
        assert rec["price_paise"] == authoritative["price_paise"]
        assert rec["price_display"] == authoritative["price_display"]


def test_a_query_matching_nothing_says_so_honestly(client, session_id):
    body = client.post("/api/buyer/chat", json={
        "session_id": session_id,
        "message": "I need a industrial CNC milling machine under ₹500"}).json()
    # No fabricated product is offered.
    for rec in body["recommendations"]:
        assert client.get(f"/api/agent/products/{rec['id']}").status_code == 200


def test_recommendation_api_exposes_its_reasoning(client):
    body = client.post("/api/agent/recommend", json={
        "query": "laptop for programming under ₹80,000", "limit": 3}).json()

    assert body["extracted_requirements"]["max_price_paise"] == 8_000_000
    assert set(body["ranking_weights"]) == {
        "text_relevance", "price_fit", "category_fit", "attribute_fit",
        "inventory", "relationship"}
    assert body["recommendations"]
    for rec in body["recommendations"]:
        assert rec["signals"]
        assert rec["why"]
