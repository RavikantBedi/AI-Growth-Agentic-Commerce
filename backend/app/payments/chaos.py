"""Controlled failure injection for the payment provider.

Wraps any `PaymentProvider` so an operator can demonstrate the two provider
failure modes on demand, against the real code path, without editing config or
unplugging the network:

  outage      -> every provider call raises PaymentProviderUnavailable
  verify_fail -> verification returns VERIFICATION_FAILED even for a good
                 signature, standing in for a provider that stops agreeing
                 with a callback the browser already accepted

The proxy only ever *adds* failures. It can never turn a failed payment into a
successful one, so it cannot be used to fake a paid order.
"""
from __future__ import annotations

import logging

from ..domain.states import PaymentStatus
from .base import (PaymentProvider, PaymentProviderUnavailable, ProviderOrder,
                   ProviderPayment, VerificationResult)

log = logging.getLogger("payments.chaos")


class ChaosProxy(PaymentProvider):
    def __init__(self, inner: PaymentProvider):
        self.inner = inner
        self.force_outage = False
        self.force_verification_failure = False

    # Identity is always the wrapped provider's — the proxy never disguises it.
    @property
    def name(self) -> str:  # type: ignore[override]
        return self.inner.name

    @property
    def is_test_mode(self) -> bool:  # type: ignore[override]
        return self.inner.is_test_mode

    @property
    def display_label(self) -> str:  # type: ignore[override]
        return self.inner.display_label

    @property
    def supports_refund(self) -> bool:  # type: ignore[override]
        return self.inner.supports_refund

    def set_failure_mode(self, *, outage: bool | None = None,
                         verification_failure: bool | None = None) -> dict:
        if outage is not None:
            self.force_outage = bool(outage)
            if hasattr(self.inner, "force_outage"):
                self.inner.force_outage = bool(outage)
        if verification_failure is not None:
            self.force_verification_failure = bool(verification_failure)
        log.warning("payment failure injection: outage=%s verification_failure=%s",
                    self.force_outage, self.force_verification_failure)
        return self.failure_mode()

    def failure_mode(self) -> dict:
        return {"outage": self.force_outage,
                "verification_failure": self.force_verification_failure}

    def _guard(self) -> None:
        if self.force_outage:
            raise PaymentProviderUnavailable(
                f"[failure injection] {self.inner.display_label} is unreachable. "
                "The payment state is unknown; the order will not be marked paid."
            )

    # -- delegation --------------------------------------------------------
    def create_order(self, **kwargs) -> ProviderOrder:
        self._guard()
        return self.inner.create_order(**kwargs)

    def get_payment(self, payment_id: str) -> ProviderPayment:
        self._guard()
        return self.inner.get_payment(payment_id)

    def get_payments_for_order(self, provider_order_id: str) -> list[ProviderPayment]:
        self._guard()
        return self.inner.get_payments_for_order(provider_order_id)

    def verify_payment(self, **kwargs) -> VerificationResult:
        self._guard()
        if self.force_verification_failure:
            return VerificationResult(
                verified=False,
                status=PaymentStatus.VERIFICATION_FAILED,
                reason=("[failure injection] Server-side verification was forced to "
                        "fail. The order is NOT marked paid and the discrepancy is "
                        "recorded for reconciliation."),
                signature_valid=False,
            )
        return self.inner.verify_payment(**kwargs)

    def capture_payment_if_required(self, payment: ProviderPayment) -> ProviderPayment:
        self._guard()
        return self.inner.capture_payment_if_required(payment)

    def refund_payment_if_supported(self, payment_id: str,
                                    amount_paise: int | None = None) -> dict:
        self._guard()
        return self.inner.refund_payment_if_supported(payment_id, amount_paise)

    def public_config(self) -> dict:
        cfg = self.inner.public_config()
        cfg["failure_injection"] = self.failure_mode()
        return cfg

    def health(self) -> dict:
        if self.force_outage:
            return {"provider": self.inner.name, "reachable": False,
                    "authenticated": False, "test_mode": self.inner.is_test_mode,
                    "error": "failure injection: outage enabled"}
        h = self.inner.health()
        h["failure_injection"] = self.failure_mode()
        return h

    # Sandbox-only passthrough used by the demo checkout endpoint.
    def attempt_payment(self, *args, **kwargs) -> dict:
        self._guard()
        if not hasattr(self.inner, "attempt_payment"):
            raise AttributeError(
                f"{self.inner.name} has no local attempt_payment; complete the "
                "payment through the provider's own checkout."
            )
        return self.inner.attempt_payment(*args, **kwargs)


__all__ = ["ChaosProxy"]
