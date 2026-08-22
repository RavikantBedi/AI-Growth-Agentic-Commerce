"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import (agent_api, audit_api, buyer, merchant_api, payments_api,
                  wellknown)
from .config import settings
from .db import engine, init_db, session_scope
from .domain.states import IllegalTransition
from .observability import RequestContextMiddleware, configure_logging
from .payments import get_payment_provider

log = logging.getLogger("app")

DESCRIPTION = """
**AI Merchant Growth & Agentic Checkout** — Track 01.

Two connected capabilities:

* an **AI growth agent** that sells conversationally with bounded upsell and
  cross-sell drawn from real catalog relationships, and
* an **agent-commerce surface** that makes the merchant discoverable,
  understandable and safely transactable by an automated buyer.

Every money action is explainable, bounded and gated:

    INTENT → POLICY CHECK → EXPLANATION → USER APPROVAL → ACTION
           → SERVER VERIFICATION → AUDIT

The AI cannot create, confirm, capture or verify a payment. Start at
`/.well-known/agent-commerce.json`.

**All payments are TEST MODE.**
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    init_db()

    with session_scope() as db:
        from .seed import seed_database
        from .services.catalog import list_products
        _, count = list_products(db, active_only=False, limit=1)
        if count == 0:
            result = seed_database(db)
            log.info("seeded demo catalog: %s products", result["total_products"])

    provider = get_payment_provider()
    log.info("payment provider: %s (%s)", provider.name, provider.display_label)
    if provider.name == "local_sandbox":
        log.warning(
            "Razorpay credentials are not configured. Payments run through the LOCAL "
            "SANDBOX provider — simulated locally, NOT Razorpay. Set RAZORPAY_KEY_ID "
            "and RAZORPAY_KEY_SECRET (test mode) to use the real Razorpay integration."
        )

    from .ai.provider import provider_status
    status = provider_status()
    log.info("LLM provider: %s (%s)", status["active"]["provider"],
             status["active"]["model"])

    yield
    engine.dispose()


app = FastAPI(
    title="AI Merchant Growth & Agentic Checkout",
    description=DESCRIPTION,
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "discovery", "description": "Machine-readable merchant manifest."},
        {"name": "agent-commerce", "description": "Endpoints designed for AI buyers."},
        {"name": "buyer", "description": "Conversational buyer interface."},
        {"name": "payments", "description": "Test-mode payment gate and verification."},
        {"name": "merchant", "description": "Merchant console."},
        {"name": "audit", "description": "Audit trail and explainability."},
    ],
)

app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-Id"],
)

app.include_router(wellknown.router)
app.include_router(agent_api.router)
app.include_router(buyer.router)
app.include_router(payments_api.router)
app.include_router(merchant_api.router)
app.include_router(audit_api.router)


@app.exception_handler(IllegalTransition)
async def illegal_transition_handler(request: Request, exc: IllegalTransition):
    """Safety net: a refused state change is a 409, never a 500."""
    log.warning("refused %s transition %s -> %s on %s",
                exc.kind, exc.current, exc.target, request.url.path)
    return JSONResponse(status_code=409, content={
        "error": "illegal_state_transition",
        "message": (f"This {exc.kind} is {exc.current} and cannot move to "
                    f"{exc.target}. Nothing was changed and nothing was charged."),
        "detail": {"kind": exc.kind, "current": exc.current, "target": exc.target},
        "request_id": getattr(request.state, "request_id", ""),
    })


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak an internal error message — or a secret — to a client."""
    request_id = getattr(request.state, "request_id", "")
    log.exception("unhandled error on %s %s", request.method, request.url.path,
                  extra={"request_id": request_id})
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error",
                 "message": "Something went wrong on our side. Nothing was charged.",
                 "request_id": request_id},
    )


@app.get("/api/health", tags=["discovery"], summary="Service health")
def health():
    from .ai.provider import provider_status
    provider = get_payment_provider()
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "test_mode": True,
        "payment_provider": {"name": provider.name, "label": provider.display_label,
                             "simulated": provider.name == "local_sandbox"},
        "llm": provider_status()["active"],
        "guardrails": {
            "max_order_value_paise": settings.max_order_value_paise,
            "max_discount_percent": settings.max_discount_percent,
            "require_payment_confirmation": settings.require_payment_confirmation,
        },
    }


@app.get("/", tags=["discovery"], summary="Service index")
def index():
    return {
        "name": "AI Merchant Growth & Agentic Checkout",
        "test_mode": True,
        "docs": "/docs",
        "manifest": "/.well-known/agent-commerce.json",
        "agent_catalog": "/api/agent/catalog",
        "health": "/api/health",
    }


__all__ = ["app"]
