"""Test fixtures.

Each test session runs against its own throwaway SQLite file and the offline
sandbox payment provider, so the suite needs no network, no Razorpay account
and no Ollama install.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Must be set before app.config is imported anywhere.
_TMP = Path(tempfile.mkdtemp(prefix="agentic-commerce-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["LLM_PROVIDER"] = "mock"          # deterministic, no network
os.environ["RAZORPAY_KEY_ID"] = ""           # blank key id forces the offline sandbox
# Sentinel secret values. Nothing uses them (the sandbox provider is active),
# but the security sweep asserts these exact strings never appear in any
# response — a far stronger check than looking for the variable *names*, which
# legitimately appear in the prompt-injection test fixture.
SENTINEL_RAZORPAY_SECRET = "test_sentinel_rzp_secret_do_not_leak_9f3a"
SENTINEL_ANTHROPIC_KEY = "sk-ant-sentinel-do-not-leak-9f3a"
os.environ["RAZORPAY_KEY_SECRET"] = SENTINEL_RAZORPAY_SECRET
os.environ["ANTHROPIC_API_KEY"] = SENTINEL_ANTHROPIC_KEY
os.environ["MAX_ORDER_VALUE"] = "100000"
os.environ["MAX_DISCOUNT_PERCENT"] = "20"
os.environ["REQUIRE_PAYMENT_CONFIRMATION"] = "true"
os.environ["TAX_PERCENT"] = "18"
os.environ["APP_ENV"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.payments import get_payment_provider, reset_payment_provider  # noqa: E402
from app.seed import seed_database  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _clean_state(db):
    """Reset catalog and transactional data before every test."""
    seed_database(db, reset=True)
    db.commit()
    reset_payment_provider()
    yield
    provider = get_payment_provider()
    provider.set_failure_mode(outage=False, verification_failure=False)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sku_index(db):
    from sqlalchemy import select

    from app.models import Product
    return {p.sku: p for p in db.scalars(select(Product)).all()}


@pytest.fixture
def session_id(client) -> str:
    resp = client.post("/api/buyer/session", json={"actor_type": "human",
                                                   "actor_label": "test-buyer",
                                                   "channel": "web"})
    assert resp.status_code == 200, resp.text
    return resp.json()["session_id"]


def product_id(client, sku: str) -> str:
    """Look up a product id by SKU through the public API."""
    resp = client.post("/api/agent/search", json={"query": sku, "limit": 50,
                                                  "in_stock_only": False})
    assert resp.status_code == 200, resp.text
    for item in resp.json()["results"]:
        if item["sku"] == sku:
            return item["id"]
    resp = client.get("/api/agent/products", params={"limit": 500})
    for item in resp.json()["products"]:
        if item["sku"] == sku:
            return item["id"]
    raise AssertionError(f"SKU {sku} not found in catalog")
