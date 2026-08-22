"""Idempotency: no duplicate orders or payments, however the request arrives."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.domain.idempotency import (IdempotencyConflict, fingerprint, idempotent)
from app.models import Transaction

from .conftest import product_id


def _quote(client, session_id, sku="LAP-DEV-001"):
    pid = product_id(client, sku)
    client.post("/api/buyer/cart/add", json={
        "session_id": session_id, "product_id": pid, "quantity": 1})
    resp = client.post("/api/payments/prepare", json={"session_id": session_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_double_click_creates_one_payment_order(client, session_id, db):
    quote = _quote(client, session_id)
    body = {"quote_id": quote["quote_id"], "confirmed": True}

    first = client.post("/api/payments/confirm", json=body).json()
    second = client.post("/api/payments/confirm", json=body).json()

    assert first["provider_order_id"] == second["provider_order_id"]
    assert first["replayed"] is False
    assert second["replayed"] is True

    db.expire_all()
    count = db.query(Transaction).filter(
        Transaction.order_id == quote["order_id"]).count()
    assert count == 1, "a repeated confirmation must not create a second transaction"


def test_browser_refresh_replays_rather_than_recharging(client, session_id):
    quote = _quote(client, session_id)
    body = {"quote_id": quote["quote_id"], "confirmed": True}

    responses = [client.post("/api/payments/confirm", json=body).json()
                 for _ in range(5)]
    order_ids = {r["provider_order_id"] for r in responses}
    assert len(order_ids) == 1
    assert sum(1 for r in responses if r["replayed"]) == 4


def test_explicit_idempotency_key_is_honoured(client, session_id):
    quote = _quote(client, session_id)
    body = {"quote_id": quote["quote_id"], "confirmed": True,
            "idempotency_key": "client-generated-key-1"}

    first = client.post("/api/payments/confirm", json=body).json()
    second = client.post("/api/payments/confirm", json=body).json()
    assert first["provider_order_id"] == second["provider_order_id"]
    assert second["replayed"] is True


def test_concurrent_confirmations_produce_one_order(client, session_id, db):
    """Two racing requests: exactly one payment order, no duplicate charge."""
    quote = _quote(client, session_id)
    body = {"quote_id": quote["quote_id"], "confirmed": True}

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(
            lambda _: client.post("/api/payments/confirm", json=body), range(4)))

    successes = [r.json() for r in responses if r.status_code == 200]
    conflicts = [r for r in responses if r.status_code == 409]
    assert successes, "at least one request must succeed"
    assert len({s["provider_order_id"] for s in successes}) == 1
    assert len(successes) + len(conflicts) == 4

    db.expire_all()
    assert db.query(Transaction).filter(
        Transaction.order_id == quote["order_id"]).count() == 1


def test_reusing_a_key_for_a_different_request_is_a_conflict(db):
    def make(value):
        return lambda: {"created": value}

    first, replayed = idempotent(db, "shared-key", "test",
                                {"amount": 100}, make("a"))
    assert (first, replayed) == ({"created": "a"}, False)

    again, replayed = idempotent(db, "shared-key", "test",
                                 {"amount": 100}, make("b"))
    assert (again, replayed) == ({"created": "a"}, True), "must replay, not re-execute"

    with pytest.raises(IdempotencyConflict):
        idempotent(db, "shared-key", "test", {"amount": 999}, make("c"))
    db.rollback()


def test_a_failed_operation_releases_its_key_for_retry(db):
    def boom():
        raise RuntimeError("provider blew up")

    with pytest.raises(RuntimeError):
        idempotent(db, "retry-key", "test", {"amount": 1}, boom)

    # The corrected retry with the same key must be allowed through.
    result, replayed = idempotent(db, "retry-key", "test", {"amount": 1},
                                  lambda: {"ok": True})
    assert result == {"ok": True}
    assert replayed is False
    db.rollback()


def test_fingerprint_is_stable_and_order_independent():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


def test_repeat_verification_is_safe(client, session_id):
    quote = _quote(client, session_id)
    payment = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": True}).json()
    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": "success"}).json()
    verify_body = {
        "razorpay_order_id": attempt["provider_order_id"],
        "razorpay_payment_id": attempt["provider_payment_id"],
        "razorpay_signature": attempt["signature"]}

    first = client.post("/api/payments/verify", json=verify_body).json()
    second = client.post("/api/payments/verify", json=verify_body).json()

    assert first["paid"] is True
    assert second["paid"] is True
    assert second["replayed"] is True

    order = client.get(f"/api/payments/order/{quote['order_id']}").json()
    assert order["status"] == "PAID"
    assert len(order["transactions"]) == 1


def test_a_consumed_quote_cannot_be_paid_twice_with_a_new_key(client, session_id):
    """A second payment order for an already-paid cart must not be creatable."""
    quote = _quote(client, session_id)
    payment = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": True}).json()
    attempt = client.post("/api/payments/sandbox/pay", json={
        "provider_order_id": payment["provider_order_id"], "outcome": "success"}).json()
    client.post("/api/payments/verify", json={
        "razorpay_order_id": attempt["provider_order_id"],
        "razorpay_payment_id": attempt["provider_payment_id"],
        "razorpay_signature": attempt["signature"]})

    retry = client.post("/api/payments/confirm", json={
        "quote_id": quote["quote_id"], "confirmed": True,
        "idempotency_key": "a-brand-new-key"})
    assert retry.status_code >= 400, "a paid order must not accept another payment"
