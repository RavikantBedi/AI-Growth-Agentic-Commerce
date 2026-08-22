"""Server-side idempotency for money-creating requests.

A double-clicked "Confirm & Pay", a browser refresh, or a retried network call
must never produce two payment orders. Callers wrap the money action in
`idempotent()`; the first caller executes, everyone else replays the stored
response.

The guarantee is enforced by the primary-key constraint on
`idempotency_records.key`, not by a read-then-write check, so two concurrent
requests cannot both pass the gate.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import IdempotencyRecord


class IdempotencyConflict(Exception):
    """Same key replayed with a materially different request body."""


class IdempotencyInProgress(Exception):
    """A concurrent request holding this key has not finished yet."""


def fingerprint(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def idempotent(
    db: Session,
    key: str,
    scope: str,
    request_payload: dict,
    fn: Callable[[], dict],
) -> tuple[dict, bool]:
    """Run `fn` at most once per (scope, key).

    Returns (response, replayed). `replayed=True` means a stored response was
    returned and `fn` was not executed.
    """
    full_key = f"{scope}:{key}"
    fp = fingerprint(request_payload)

    existing = db.get(IdempotencyRecord, full_key)
    if existing is not None:
        if existing.request_fingerprint != fp:
            raise IdempotencyConflict(
                f"Idempotency key '{key}' was already used for a different request. "
                "Use a fresh key for a different cart or amount."
            )
        if existing.state == "done":
            return existing.response_json, True
        raise IdempotencyInProgress(
            f"A request with idempotency key '{key}' is still in progress."
        )

    # Claim the key first. If a concurrent request wins the race, the unique
    # constraint fires and we fall back to the replay path.
    record = IdempotencyRecord(
        key=full_key, scope=scope, request_fingerprint=fp,
        response_json={}, state="in_progress",
    )
    db.add(record)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.get(IdempotencyRecord, full_key)
        if existing and existing.state == "done":
            if existing.request_fingerprint != fp:
                raise IdempotencyConflict(
                    f"Idempotency key '{key}' was already used for a different request."
                ) from None
            return existing.response_json, True
        raise IdempotencyInProgress(
            f"A request with idempotency key '{key}' is still in progress."
        ) from None

    try:
        response = fn()
    except Exception:
        # Release the claim so a corrected retry with the same key can proceed.
        db.rollback()
        stale = db.get(IdempotencyRecord, full_key)
        if stale is not None and stale.state != "done":
            db.delete(stale)
            db.commit()
        raise

    record.response_json = response
    record.state = "done"
    db.add(record)
    db.flush()
    return response, False


__all__ = ["idempotent", "fingerprint", "IdempotencyConflict", "IdempotencyInProgress"]
