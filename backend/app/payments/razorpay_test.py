"""Razorpay **test mode** provider, over the documented REST API.

Endpoints actually used (all under https://api.razorpay.com/v1, HTTP Basic auth
with key_id / key_secret):

  POST /orders                      create an order       (Orders API)
  GET  /orders/{id}                 fetch an order
  GET  /orders/{id}/payments        payments for an order
  GET  /payments/{id}               fetch a payment
  POST /payments/{id}/capture       capture an authorized payment
  POST /payments/{id}/refund        refund (test mode supports this)

Signature verification is the standard Razorpay Checkout scheme:

    HMAC_SHA256(razorpay_order_id + "|" + razorpay_payment_id, key_secret)
        == razorpay_signature

Nothing here is invented: no endpoint or field is used that Razorpay does not
document. The secret key never leaves the backend.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from ..domain.states import PaymentStatus
from .base import (PaymentProvider, PaymentProviderError,
                   PaymentProviderUnavailable, ProviderOrder, ProviderPayment,
                   VerificationResult)

log = logging.getLogger("payments.razorpay")

API_BASE = "https://api.razorpay.com/v1"

#: Razorpay payment.status -> our internal payment state.
_STATUS_MAP = {
    "created": PaymentStatus.PENDING,
    "authorized": PaymentStatus.AUTHORIZED,
    "captured": PaymentStatus.CAPTURED,
    "refunded": PaymentStatus.REFUNDED,
    "failed": PaymentStatus.FAILED,
}


class RazorpayTestProvider(PaymentProvider):
    name = "razorpay_test"
    is_test_mode = True
    display_label = "Razorpay — TEST MODE"
    supports_refund = True

    def __init__(self, key_id: str, key_secret: str, timeout: float = 15.0):
        if not key_id or not key_secret:
            raise PaymentProviderError("Razorpay credentials are not configured.",
                                       code="not_configured")
        if key_id.startswith("rzp_live_"):
            # Belt and braces: config.py also refuses live keys.
            raise PaymentProviderError(
                "Refusing to initialise with a LIVE Razorpay key. Test mode only.",
                code="live_key_refused",
            )
        self.key_id = key_id
        self._key_secret = key_secret
        self.timeout = timeout
        self._auth_header = "Basic " + base64.b64encode(
            f"{key_id}:{key_secret}".encode()
        ).decode()

    # -- transport ---------------------------------------------------------
    def _request(self, method: str, path: str, *, json_body: dict | None = None) -> dict:
        url = f"{API_BASE}{path}"
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.request(
                    method, url,
                    headers={"Authorization": self._auth_header,
                             "Content-Type": "application/json"},
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            log.warning("razorpay transport failure %s %s: %s", method, path, exc)
            raise PaymentProviderUnavailable(
                f"Could not reach Razorpay ({type(exc).__name__}). "
                "The payment state is unknown and the order will not be marked paid."
            ) from exc

        duration_ms = (time.perf_counter() - started) * 1000
        log.info("razorpay %s %s -> %s in %.0fms", method, path, resp.status_code, duration_ms)

        if resp.status_code >= 500:
            raise PaymentProviderUnavailable(
                f"Razorpay returned {resp.status_code}. Payment state is unknown.",
                raw=_safe_json(resp),
            )
        payload = _safe_json(resp)
        if resp.status_code >= 400:
            err = (payload or {}).get("error", {}) if isinstance(payload, dict) else {}
            raise PaymentProviderError(
                err.get("description") or f"Razorpay rejected the request ({resp.status_code}).",
                code=err.get("code", "razorpay_error"),
                http_status=resp.status_code,
                raw=payload,
            )
        return payload if isinstance(payload, dict) else {}

    # -- interface ---------------------------------------------------------
    def create_order(self, *, amount_paise: int, currency: str, receipt: str,
                     notes: dict | None = None) -> ProviderOrder:
        # Razorpay expects the amount in the smallest currency unit; our whole
        # ledger is already in paise so there is no conversion here.
        body: dict[str, Any] = {
            "amount": int(amount_paise),
            "currency": currency,
            "receipt": receipt[:40],
            "payment_capture": 1,   # auto-capture on successful authorization
        }
        if notes:
            body["notes"] = {str(k)[:256]: str(v)[:512] for k, v in list(notes.items())[:15]}

        data = self._request("POST", "/orders", json_body=body)
        return ProviderOrder(
            id=data["id"],
            amount_paise=int(data.get("amount", amount_paise)),
            currency=data.get("currency", currency),
            status=data.get("status", "created"),
            receipt=data.get("receipt"),
            raw=data,
        )

    def get_order(self, provider_order_id: str) -> ProviderOrder:
        data = self._request("GET", f"/orders/{provider_order_id}")
        return ProviderOrder(
            id=data["id"], amount_paise=int(data.get("amount", 0)),
            currency=data.get("currency", "INR"), status=data.get("status", "created"),
            receipt=data.get("receipt"), raw=data,
        )

    def get_payment(self, payment_id: str) -> ProviderPayment:
        return _to_payment(self._request("GET", f"/payments/{payment_id}"))

    def get_payments_for_order(self, provider_order_id: str) -> list[ProviderPayment]:
        data = self._request("GET", f"/orders/{provider_order_id}/payments")
        return [_to_payment(item) for item in data.get("items", [])]

    def verify_payment(self, *, provider_order_id: str, provider_payment_id: str,
                       signature: str, expected_amount_paise: int) -> VerificationResult:
        """Two independent checks, both must pass.

        1. The HMAC signature proves the callback really came from Razorpay.
        2. A server-side fetch of the payment proves it is actually captured
           for the exact amount we asked for. A valid signature alone is not
           accepted as proof of payment.
        """
        expected_sig = hmac.new(
            self._key_secret.encode(),
            f"{provider_order_id}|{provider_payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        signature_valid = hmac.compare_digest(expected_sig, (signature or "").strip())

        if not signature_valid:
            return VerificationResult(
                verified=False,
                status=PaymentStatus.VERIFICATION_FAILED,
                reason=("Razorpay signature did not match. The payment confirmation "
                        "could not be proven authentic, so the order was NOT marked paid."),
                signature_valid=False,
            )

        # Signature is good; now confirm with Razorpay itself.
        payment = self.get_payment(provider_payment_id)

        if payment.order_id and payment.order_id != provider_order_id:
            return VerificationResult(
                verified=False, status=PaymentStatus.VERIFICATION_FAILED,
                reason=(f"Payment {provider_payment_id} belongs to order "
                        f"{payment.order_id}, not {provider_order_id}."),
                signature_valid=True, payment=payment,
            )

        amount_matched = payment.amount_paise == expected_amount_paise
        if not amount_matched:
            return VerificationResult(
                verified=False, status=PaymentStatus.VERIFICATION_FAILED,
                reason=(f"Amount mismatch: Razorpay reports {payment.amount_paise} paise "
                        f"but the approved quote was {expected_amount_paise} paise."),
                signature_valid=True, amount_matched=False, payment=payment,
            )

        if payment.status == PaymentStatus.FAILED:
            return VerificationResult(
                verified=False, status=PaymentStatus.FAILED,
                reason=(payment.error_description
                        or "Razorpay reports this payment as failed."),
                signature_valid=True, amount_matched=True, payment=payment,
            )

        if payment.status == PaymentStatus.AUTHORIZED:
            payment = self.capture_payment_if_required(payment)

        if payment.status == PaymentStatus.CAPTURED:
            return VerificationResult(
                verified=True, status=PaymentStatus.CAPTURED,
                reason=("Signature verified and Razorpay confirms the payment is "
                        f"captured for {payment.amount_paise} paise."),
                signature_valid=True, amount_matched=True, payment=payment,
            )

        return VerificationResult(
            verified=False, status=payment.status,
            reason=(f"Payment is in state '{payment.raw.get('status')}' which is not a "
                    "settled state. The order was not marked paid."),
            signature_valid=True, amount_matched=amount_matched, payment=payment,
        )

    def capture_payment_if_required(self, payment: ProviderPayment) -> ProviderPayment:
        """Capture an authorized-but-uncaptured payment.

        Orders are created with payment_capture=1 so Razorpay normally captures
        automatically; this covers the case where it did not.
        """
        if payment.status != PaymentStatus.AUTHORIZED:
            return payment
        data = self._request(
            "POST", f"/payments/{payment.id}/capture",
            json_body={"amount": payment.amount_paise, "currency": payment.currency},
        )
        return _to_payment(data)

    def refund_payment_if_supported(self, payment_id: str,
                                    amount_paise: int | None = None) -> dict:
        body = {"amount": int(amount_paise)} if amount_paise else {}
        data = self._request("POST", f"/payments/{payment_id}/refund", json_body=body)
        return {"refund_id": data.get("id"), "status": data.get("status"),
                "amount_paise": data.get("amount")}

    def public_config(self) -> dict:
        # Only the publishable key id goes to the browser. Never the secret.
        return {"provider": self.name, "test_mode": True,
                "label": self.display_label, "key_id": self.key_id,
                "checkout_script": "https://checkout.razorpay.com/v1/checkout.js"}

    def health(self) -> dict:
        """Cheap reachability probe using a documented read endpoint."""
        try:
            with httpx.Client(timeout=min(self.timeout, 6.0)) as client:
                resp = client.get(f"{API_BASE}/payments?count=1",
                                  headers={"Authorization": self._auth_header})
            reachable = resp.status_code < 500
            return {"provider": self.name, "reachable": reachable,
                    "authenticated": resp.status_code != 401,
                    "http_status": resp.status_code, "test_mode": True}
        except httpx.HTTPError as exc:
            return {"provider": self.name, "reachable": False, "authenticated": False,
                    "error": f"{type(exc).__name__}: {exc}", "test_mode": True}


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"raw_text": resp.text[:500]}


def _to_payment(data: dict) -> ProviderPayment:
    raw_status = str(data.get("status", "")).lower()
    status = _STATUS_MAP.get(raw_status, PaymentStatus.UNKNOWN)
    return ProviderPayment(
        id=data.get("id", ""),
        order_id=data.get("order_id"),
        amount_paise=int(data.get("amount") or 0),
        currency=data.get("currency", "INR"),
        status=status,
        method=data.get("method"),
        captured=bool(data.get("captured")),
        error_code=data.get("error_code"),
        error_description=data.get("error_description"),
        # Store only non-sensitive fields; card/UPI credentials are dropped.
        raw={k: data.get(k) for k in
             ("id", "order_id", "status", "method", "amount", "currency",
              "captured", "error_code", "error_description", "error_reason",
              "created_at", "bank", "wallet", "vpa")
             if k in data},
    )


__all__ = ["RazorpayTestProvider"]
