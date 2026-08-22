"""Payment provider selection.

Razorpay test mode is used whenever credentials are present. The offline
sandbox is a fallback for running with no accounts — never a silent stand-in:
its identity is surfaced in every API response, the manifest and the UI.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

from ..config import settings
from .base import (PaymentProvider, PaymentProviderError,
                   PaymentProviderUnavailable, ProviderOrder, ProviderPayment,
                   VerificationResult)
from .chaos import ChaosProxy
from .razorpay_test import RazorpayTestProvider
from .sandbox import LocalSandboxProvider, Outcome

log = logging.getLogger("payments")

_provider: ChaosProxy | None = None


def build_provider() -> ChaosProxy:
    """Construct the configured provider, wrapped for failure injection."""
    if settings.razorpay_configured:
        try:
            inner: PaymentProvider = RazorpayTestProvider(
                settings.razorpay_key_id,
                settings.razorpay_key_secret,
                timeout=settings.razorpay_timeout_seconds,
            )
            log.info("payment provider: Razorpay TEST MODE (key %s…)",
                     settings.razorpay_key_id[:14])
        except PaymentProviderError as exc:
            log.error("Razorpay init failed (%s); falling back to local sandbox.", exc)
            inner = LocalSandboxProvider()
    else:
        inner = LocalSandboxProvider()
        log.warning(
            "No RAZORPAY_KEY_ID/SECRET configured — using the LOCAL SANDBOX provider. "
            "Payments are simulated locally and are NOT Razorpay transactions."
        )
    return ChaosProxy(inner)


def get_payment_provider() -> ChaosProxy:
    global _provider
    if _provider is None:
        _provider = build_provider()
    return _provider


def reset_payment_provider() -> None:
    """Rebuild the provider — used by tests and after a settings change."""
    global _provider
    _provider = None


@contextmanager
def use_provider(provider: ChaosProxy):
    """Temporarily swap the active provider.

    Used by the growth simulator, which runs hundreds of synthetic checkouts
    through the offline sandbox rather than creating hundreds of orders against
    a real Razorpay test account. Simulated payments are labelled synthetic in
    the database and in every metric that reports them.
    """
    global _provider
    previous = _provider
    _provider = provider
    try:
        yield provider
    finally:
        _provider = previous


__all__ = [
    "PaymentProvider", "PaymentProviderError", "PaymentProviderUnavailable",
    "ProviderOrder", "ProviderPayment", "VerificationResult",
    "RazorpayTestProvider", "LocalSandboxProvider", "Outcome", "ChaosProxy",
    "get_payment_provider", "build_provider", "reset_payment_provider",
    "use_provider",
]
