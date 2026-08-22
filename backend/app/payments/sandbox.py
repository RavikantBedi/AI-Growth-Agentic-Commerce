"""Offline sandbox provider — **this is not Razorpay**.

Used only when no Razorpay test credentials are configured, so that the app is
runnable end-to-end on a machine with no accounts and no network. It is
labelled `local_sandbox` everywhere it surfaces: API responses, the manifest,
the audit trail and the UI banner. It never claims to be Razorpay.

It is deliberately *not* a stub that returns success. It issues a real
HMAC-SHA256 signature over `order_id|payment_id` using a per-process secret, so
the production verification path — signature check, server-side amount check,
state mapping — executes unchanged. A tampered signature genuinely fails
verification here, which is what makes the failure demos meaningful offline.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
import uuid
from dataclasses import dataclass

from ..domain.states import PaymentStatus
from .base import (PaymentProvider, PaymentProviderError,
                   PaymentProviderUnavailable, ProviderOrder, ProviderPayment,
                   VerificationResult)

log = logging.getLogger("payments.sandbox")


class Outcome:
    """What the operator asks the sandbox checkout to do."""
    SUCCESS = "success"
    FAILURE = "failure"                    # buyer's payment declined
    TAMPERED_SIGNATURE = "tampered_signature"
    AUTHORIZED_ONLY = "authorized_only"    # needs an explicit capture
    PROVIDER_OUTAGE = "provider_outage"


@dataclass
class _SandboxOrder:
    id: str
    amount_paise: int
    currency: str
    receipt: str
    status: str = "created"
    created_at: float = 0.0


@dataclass
class _SandboxPayment:
    id: str
    order_id: str
    amount_paise: int
    currency: str
    status: PaymentStatus
    method: str = "sandbox_card"
    captured: bool = False
    error_code: str | None = None
    error_description: str | None = None


class LocalSandboxProvider(PaymentProvider):
    name = "local_sandbox"
    is_test_mode = True
    display_label = "LOCAL SANDBOX — simulated, not Razorpay"
    supports_refund = True

    def __init__(self, secret: str | None = None):
        self._secret = secret or secrets.token_hex(32)
        self._orders: dict[str, _SandboxOrder] = {}
        self._payments: dict[str, _SandboxPayment] = {}
        self._order_payments: dict[str, list[str]] = {}
        #: Set by the merchant console to demonstrate a provider outage.
        self.force_outage: bool = False

    # -- helpers -----------------------------------------------------------
    def _sign(self, order_id: str, payment_id: str) -> str:
        return hmac.new(self._secret.encode(),
                        f"{order_id}|{payment_id}".encode(),
                        hashlib.sha256).hexdigest()

    def _guard(self) -> None:
        if self.force_outage:
            raise PaymentProviderUnavailable(
                "Sandbox outage is enabled: the payment provider is unreachable. "
                "Payment state is unknown; the order will not be marked paid."
            )

    # -- interface ---------------------------------------------------------
    def create_order(self, *, amount_paise: int, currency: str, receipt: str,
                     notes: dict | None = None) -> ProviderOrder:
        self._guard()
        if amount_paise <= 0:
            raise PaymentProviderError("Amount must be greater than zero.",
                                       code="invalid_amount")
        oid = f"sbx_order_{uuid.uuid4().hex[:14]}"
        order = _SandboxOrder(id=oid, amount_paise=int(amount_paise), currency=currency,
                              receipt=receipt, created_at=time.time())
        self._orders[oid] = order
        self._order_payments[oid] = []
        log.info("sandbox order created %s amount_paise=%s", oid, amount_paise)
        return ProviderOrder(id=oid, amount_paise=order.amount_paise, currency=currency,
                             status="created", receipt=receipt,
                             raw={"id": oid, "simulated": True,
                                  "provider_note": "LOCAL SANDBOX — not a Razorpay order"})

    def attempt_payment(self, provider_order_id: str, outcome: str = Outcome.SUCCESS,
                        amount_paise_override: int | None = None) -> dict:
        """Stand-in for the hosted checkout the buyer would complete.

        Returns the same triple Razorpay Checkout hands back to the browser:
        order id, payment id and signature.
        """
        self._guard()
        if outcome == Outcome.PROVIDER_OUTAGE:
            raise PaymentProviderUnavailable(
                "Simulated provider outage during checkout. Payment state is unknown."
            )
        order = self._orders.get(provider_order_id)
        if order is None:
            raise PaymentProviderError(f"Unknown sandbox order {provider_order_id}.",
                                       code="order_not_found")

        pid = f"sbx_pay_{uuid.uuid4().hex[:14]}"
        amount = amount_paise_override if amount_paise_override is not None else order.amount_paise

        if outcome == Outcome.FAILURE:
            payment = _SandboxPayment(
                id=pid, order_id=order.id, amount_paise=amount, currency=order.currency,
                status=PaymentStatus.FAILED, error_code="BAD_REQUEST_ERROR",
                error_description="Payment declined by the (simulated) issuing bank.",
            )
        elif outcome == Outcome.AUTHORIZED_ONLY:
            payment = _SandboxPayment(id=pid, order_id=order.id, amount_paise=amount,
                                      currency=order.currency,
                                      status=PaymentStatus.AUTHORIZED, captured=False)
        else:
            payment = _SandboxPayment(id=pid, order_id=order.id, amount_paise=amount,
                                      currency=order.currency,
                                      status=PaymentStatus.CAPTURED, captured=True)

        self._payments[pid] = payment
        self._order_payments.setdefault(order.id, []).append(pid)

        signature = self._sign(order.id, pid)
        if outcome == Outcome.TAMPERED_SIGNATURE:
            # A signature that is well-formed but wrong — exactly what a forged
            # client-side "payment succeeded" callback looks like.
            signature = hashlib.sha256(f"forged{pid}".encode()).hexdigest()

        return {
            "provider_order_id": order.id,
            "provider_payment_id": pid,
            "signature": signature,
            "outcome": outcome,
            "simulated": True,
        }

    def get_payment(self, payment_id: str) -> ProviderPayment:
        self._guard()
        p = self._payments.get(payment_id)
        if p is None:
            raise PaymentProviderError(f"Unknown sandbox payment {payment_id}.",
                                       code="payment_not_found")
        return self._to_public(p)

    def get_payments_for_order(self, provider_order_id: str) -> list[ProviderPayment]:
        self._guard()
        return [self._to_public(self._payments[pid])
                for pid in self._order_payments.get(provider_order_id, [])]

    def verify_payment(self, *, provider_order_id: str, provider_payment_id: str,
                       signature: str, expected_amount_paise: int) -> VerificationResult:
        self._guard()
        expected_sig = self._sign(provider_order_id, provider_payment_id)
        signature_valid = hmac.compare_digest(expected_sig, (signature or "").strip())

        if not signature_valid:
            return VerificationResult(
                verified=False, status=PaymentStatus.VERIFICATION_FAILED,
                reason=("Sandbox signature did not match. The payment confirmation "
                        "could not be proven authentic, so the order was NOT marked paid."),
                signature_valid=False,
            )

        payment = self._payments.get(provider_payment_id)
        if payment is None:
            return VerificationResult(
                verified=False, status=PaymentStatus.VERIFICATION_FAILED,
                reason=f"No sandbox payment {provider_payment_id} exists.",
                signature_valid=True,
            )
        if payment.order_id != provider_order_id:
            return VerificationResult(
                verified=False, status=PaymentStatus.VERIFICATION_FAILED,
                reason=(f"Payment {provider_payment_id} belongs to order "
                        f"{payment.order_id}, not {provider_order_id}."),
                signature_valid=True, payment=self._to_public(payment),
            )

        amount_matched = payment.amount_paise == expected_amount_paise
        if not amount_matched:
            return VerificationResult(
                verified=False, status=PaymentStatus.VERIFICATION_FAILED,
                reason=(f"Amount mismatch: provider reports {payment.amount_paise} paise "
                        f"but the approved quote was {expected_amount_paise} paise."),
                signature_valid=True, amount_matched=False,
                payment=self._to_public(payment),
            )

        if payment.status == PaymentStatus.FAILED:
            return VerificationResult(
                verified=False, status=PaymentStatus.FAILED,
                reason=payment.error_description or "Provider reports the payment failed.",
                signature_valid=True, amount_matched=True,
                payment=self._to_public(payment),
            )

        if payment.status == PaymentStatus.AUTHORIZED:
            captured = self.capture_payment_if_required(self._to_public(payment))
            payment = self._payments[captured.id]

        if payment.status == PaymentStatus.CAPTURED:
            return VerificationResult(
                verified=True, status=PaymentStatus.CAPTURED,
                reason=("Signature verified and the provider confirms the payment is "
                        f"captured for {payment.amount_paise} paise."),
                signature_valid=True, amount_matched=True,
                payment=self._to_public(payment),
            )

        return VerificationResult(
            verified=False, status=payment.status,
            reason=f"Payment is in non-settled state {payment.status.value}.",
            signature_valid=True, amount_matched=True, payment=self._to_public(payment),
        )

    def capture_payment_if_required(self, payment: ProviderPayment) -> ProviderPayment:
        self._guard()
        internal = self._payments.get(payment.id)
        if internal is None or internal.status != PaymentStatus.AUTHORIZED:
            return payment
        internal.status = PaymentStatus.CAPTURED
        internal.captured = True
        return self._to_public(internal)

    def refund_payment_if_supported(self, payment_id: str,
                                    amount_paise: int | None = None) -> dict:
        self._guard()
        p = self._payments.get(payment_id)
        if p is None:
            raise PaymentProviderError(f"Unknown sandbox payment {payment_id}.",
                                       code="payment_not_found")
        if p.status != PaymentStatus.CAPTURED:
            raise PaymentProviderError("Only captured payments can be refunded.",
                                       code="not_captured")
        p.status = PaymentStatus.REFUNDED
        return {"refund_id": f"sbx_rfnd_{uuid.uuid4().hex[:12]}", "status": "processed",
                "amount_paise": amount_paise or p.amount_paise, "simulated": True}

    def public_config(self) -> dict:
        return {
            "provider": self.name, "test_mode": True, "label": self.display_label,
            "simulated": True,
            "warning": ("No Razorpay test credentials are configured, so payments are "
                        "being simulated locally. This is NOT a Razorpay integration. "
                        "Add RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET to use real "
                        "Razorpay test mode."),
        }

    def health(self) -> dict:
        return {"provider": self.name, "reachable": not self.force_outage,
                "authenticated": True, "test_mode": True, "simulated": True,
                "force_outage": self.force_outage}

    def _to_public(self, p: _SandboxPayment) -> ProviderPayment:
        return ProviderPayment(
            id=p.id, order_id=p.order_id, amount_paise=p.amount_paise,
            currency=p.currency, status=p.status, method=p.method, captured=p.captured,
            error_code=p.error_code, error_description=p.error_description,
            raw={"id": p.id, "order_id": p.order_id, "status": p.status.value,
                 "amount": p.amount_paise, "simulated": True},
        )


__all__ = ["LocalSandboxProvider", "Outcome"]
