"""Payment provider interface.

Business logic depends on this interface, never on a vendor SDK. Two
implementations exist: `RazorpayTestProvider` (real Razorpay test-mode REST
API) and `LocalSandboxProvider` (offline fallback, loudly labelled as *not*
Razorpay so a demo can never be mistaken for a real integration).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from ..domain.states import PaymentStatus


class PaymentProviderError(Exception):
    """The provider rejected the request (4xx, bad params, business rule)."""

    def __init__(self, message: str, *, code: str = "provider_error",
                 http_status: int | None = None, raw: Any = None):
        super().__init__(message)
        self.message, self.code, self.http_status, self.raw = message, code, http_status, raw


class PaymentProviderUnavailable(PaymentProviderError):
    """The provider could not be reached at all (DNS, timeout, 5xx).

    Distinct from a rejection: the money state is *unknown*, so the order must
    never be marked PAID and never be marked definitively FAILED without a
    later reconciliation.
    """

    def __init__(self, message: str, *, raw: Any = None):
        super().__init__(message, code="provider_unavailable", raw=raw)


@dataclass
class ProviderOrder:
    id: str
    amount_paise: int
    currency: str
    status: str
    receipt: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ProviderPayment:
    id: str
    order_id: str | None
    amount_paise: int
    currency: str
    status: PaymentStatus
    method: str | None = None
    captured: bool = False
    error_code: str | None = None
    error_description: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class VerificationResult:
    """Outcome of *server-side* verification. The only thing that may mark PAID."""
    verified: bool
    status: PaymentStatus
    reason: str
    signature_valid: bool = False
    amount_matched: bool = False
    payment: ProviderPayment | None = None

    def to_dict(self) -> dict:
        return {
            "verified": self.verified,
            "status": self.status.value,
            "reason": self.reason,
            "signature_valid": self.signature_valid,
            "amount_matched": self.amount_matched,
            "provider_payment_id": self.payment.id if self.payment else None,
            "method": self.payment.method if self.payment else None,
        }


class PaymentProvider(abc.ABC):
    name: str = "abstract"
    is_test_mode: bool = True
    #: Shown in the UI so the operator always knows what they are talking to.
    display_label: str = "Abstract provider"
    supports_refund: bool = False

    @abc.abstractmethod
    def create_order(self, *, amount_paise: int, currency: str, receipt: str,
                     notes: dict | None = None) -> ProviderOrder:
        ...

    @abc.abstractmethod
    def get_payment(self, payment_id: str) -> ProviderPayment:
        ...

    @abc.abstractmethod
    def get_payments_for_order(self, provider_order_id: str) -> list[ProviderPayment]:
        ...

    @abc.abstractmethod
    def verify_payment(self, *, provider_order_id: str, provider_payment_id: str,
                       signature: str, expected_amount_paise: int) -> VerificationResult:
        ...

    @abc.abstractmethod
    def capture_payment_if_required(self, payment: ProviderPayment) -> ProviderPayment:
        ...

    def refund_payment_if_supported(self, payment_id: str,
                                    amount_paise: int | None = None) -> dict:
        raise PaymentProviderError(
            f"{self.name} does not support refunds in this integration.",
            code="refund_unsupported",
        )

    def public_config(self) -> dict:
        """Non-secret values the browser may receive."""
        return {"provider": self.name, "test_mode": self.is_test_mode,
                "label": self.display_label}

    @abc.abstractmethod
    def health(self) -> dict:
        ...


__all__ = [
    "PaymentProvider", "PaymentProviderError", "PaymentProviderUnavailable",
    "ProviderOrder", "ProviderPayment", "VerificationResult",
]
