"""Growth simulation — a measurable A/B experiment on synthetic buyers.

Two arms run through the *real* code path — the same retrieval, ranking,
cart, pricing, policy engine, checkout gate and payment verification the live
buyer uses:

  baseline      upsell and cross-sell suppressed
  ai_assisted   bounded upsell and cross-sell enabled

What is synthetic, stated plainly:

  * the buyer personas and their messages
  * whether a simulated buyer converts or accepts an add-on — drawn from a
    seeded RNG, not observed behaviour
  * the payments, which run through the offline sandbox provider so a run does
    not create hundreds of orders against a real Razorpay test account

What is real: the products, the prices, the discounts, the tax, the policy
decisions, the state machine, the signature verification, and every number in
the report — all computed from rows this run actually wrote.

Results are labelled SYNTHETIC everywhere and are never mixed into live metrics.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..domain.money import format_inr
from ..models import ExperimentRun
from ..payments import ChaosProxy, LocalSandboxProvider, Outcome, use_provider
from . import cart as cart_service
from . import catalog as catalog_service
from . import checkout as checkout_service
from . import metrics as metrics_service
from . import recommend as recommend_service
from . import upsell as upsell_service
from .merchant import get_merchant

log = logging.getLogger("services.simulation")

PERSONAS: list[dict] = [
    {"label": "developer", "message": "I need a laptop for programming under ₹80,000"},
    {"label": "gamer", "message": "I need a gaming setup under ₹1,20,000"},
    {"label": "budget-phone", "message": "I need a phone under ₹30,000"},
    {"label": "traveller", "message": "I need a camera for travel photography under ₹70,000"},
    {"label": "student", "message": "I need a cheap laptop for study under ₹45,000"},
    {"label": "audiophile", "message": "I want good headphones for music under ₹25,000"},
    {"label": "creator", "message": "I need a camera for vlogging under ₹90,000"},
    {"label": "office", "message": "I need a monitor for office work under ₹35,000"},
]

#: Seeded behavioural assumptions for the synthetic buyers. Stated here rather
#: than buried, because these numbers shape the result and are NOT observed.
BASE_PURCHASE_PROBABILITY = 0.55
UPSELL_ACCEPT_PROBABILITY = 0.45
PAYMENT_SUCCESS_PROBABILITY = 0.92   # the rest exercise the failure path for real


@dataclass
class ArmResult:
    variant: str
    sessions: int
    metrics: dict


def run_simulation(db: Session, *, sessions_per_arm: int = 25, seed: int = 1337,
                   label: str = "Baseline vs AI-assisted upsell") -> dict:
    """Run both arms and return a comparison computed from what was written."""
    started = time.perf_counter()
    merchant = get_merchant(db)
    experiment = ExperimentRun(label=label, sessions_per_arm=sessions_per_arm,
                               is_synthetic=True)
    db.add(experiment)
    db.flush()

    # A dedicated sandbox for the whole run: real verification logic, no calls
    # to an external provider.
    sandbox = ChaosProxy(LocalSandboxProvider())

    with use_provider(sandbox):
        for variant in ("baseline", "ai_assisted"):
            for i in range(sessions_per_arm):
                persona = PERSONAS[i % len(PERSONAS)]
                # Paired draws: buyer i in both arms is the *same* synthetic
                # buyer, with the same purchase decision and the same payment
                # outcome. Only the upsell acceptance draw is arm-specific,
                # because only the AI-assisted arm ever asks. Independent
                # streams per arm would let RNG noise show up as a conversion
                # difference that the experimental variable cannot cause.
                shared = random.Random(f"{seed}:buyer:{i}")
                upsell_rng = random.Random(f"{seed}:upsell:{i}")
                try:
                    _run_one_session(db, experiment.id, variant, persona, shared,
                                     upsell_rng, merchant)
                except Exception as exc:  # a broken persona must not kill the run
                    log.warning("simulated session failed (%s/%s): %s",
                                variant, persona["label"], exc)
                    db.rollback()
    db.commit()

    baseline = metrics_service.compute(db, scope="synthetic",
                                       experiment_id=experiment.id, variant="baseline")
    assisted = metrics_service.compute(db, scope="synthetic",
                                       experiment_id=experiment.id, variant="ai_assisted")

    comparison = _compare(baseline, assisted, sessions_per_arm)
    results = {
        "experiment_id": experiment.id,
        "label": label,
        "sessions_per_arm": sessions_per_arm,
        "seed": seed,
        "duration_seconds": round(time.perf_counter() - started, 2),
        "baseline": baseline.to_dict(),
        "ai_assisted": assisted.to_dict(),
        "comparison": comparison,
        "assumptions": {
            "base_purchase_probability": BASE_PURCHASE_PROBABILITY,
            "upsell_accept_probability": UPSELL_ACCEPT_PROBABILITY,
            "payment_success_probability": PAYMENT_SUCCESS_PROBABILITY,
            "paired_draws": ("Buyer i is the same synthetic buyer in both arms, with "
                             "the same purchase decision and payment outcome. Only the "
                             "add-on acceptance draw differs, so any conversion "
                             "difference between arms would be a bug, not a finding."),
            "note": ("These are seeded behavioural assumptions for synthetic buyers, "
                     "not observed conversion data. They determine whether a simulated "
                     "shopper buys or accepts an add-on. Everything downstream — "
                     "prices, discounts, tax, policy decisions, payment verification "
                     "and the reported totals — is produced by the real code path."),
        },
        "payment_note": ("Simulated checkouts use the offline sandbox provider so a run "
                         "does not create hundreds of orders on a real Razorpay test "
                         "account. Signature verification, the payment state machine and "
                         "the order state machine all execute normally, including the "
                         "failure path."),
        "label_warning": "SYNTHETIC / DEMO DATA — not real merchant revenue.",
    }
    experiment.results = results
    db.commit()
    return results


def _run_one_session(db: Session, experiment_id: str, variant: str, persona: dict,
                     rng: random.Random, upsell_rng: random.Random, merchant) -> None:
    """One synthetic buyer, start to finish, through the production pipeline.

    `rng` carries the decisions both arms share (does this buyer purchase at
    all, does the payment succeed). `upsell_rng` carries only the add-on
    acceptance, which exists in the AI-assisted arm alone.
    """
    session = cart_service.get_or_create_session(
        db, actor_type="ai_agent", actor_label=f"sim:{persona['label']}",
        channel="simulation", is_synthetic=True, variant=variant,
        experiment_id=experiment_id, meta={"persona": persona["label"]},
    )

    # 1. Real retrieval and ranking against the real catalog.
    requirements = recommend_service.extract_requirements(
        persona["message"], known_brands=catalog_service.brands(db))
    candidates = recommend_service.rank_candidates(db, requirements, limit=5)
    if not candidates:
        db.commit()
        return

    # 2. Does this synthetic shopper buy at all?
    if rng.random() > BASE_PURCHASE_PROBABILITY:
        db.commit()   # an abandoned session, which the metrics count honestly
        return

    top = candidates[0].product
    try:
        cart_service.add_item(db, session.id, top.id, 1, source="direct",
                              actor="sim", actor_type="ai_agent")
    except cart_service.CartError:
        db.commit()
        return

    # 3. The experimental variable: bounded upsell, on or off.
    if variant == "ai_assisted":
        order = cart_service.get_active_cart(db, session.id)
        priced = cart_service.recalculate(db, order)
        result = upsell_service.suggest(
            db,
            [{"product_id": i.product_id, "name": i.name, "quantity": i.quantity}
             for i in order.items],
            subtotal_paise=priced.subtotal_paise,
            max_order_value_paise=merchant.max_order_value_paise,
            upsell_enabled=merchant.upsell_enabled,
            cross_sell_enabled=merchant.cross_sell_enabled,
            tax_percent=merchant.tax_percent,
        )
        if result.suggestions:
            from ..domain import audit
            audit.record(
                db, audit.Action.UPSELL_SUGGESTED, session_id=session.id,
                order_id=order.id, actor="sim", actor_type="ai_agent",
                reason="Simulated bounded upsell offered: " + ", ".join(
                    f"{s.product.name} (+{format_inr(s.incremental_paise)})"
                    for s in result.suggestions),
                decision=audit.Decision.INFO, is_synthetic=True,
            )
            for suggestion in result.suggestions:
                if upsell_rng.random() <= UPSELL_ACCEPT_PROBABILITY:
                    try:
                        cart_service.add_item(db, session.id, suggestion.product.id, 1,
                                              source=suggestion.kind, actor="sim",
                                              actor_type="ai_agent")
                    except cart_service.CartError:
                        pass

    # 4. The real money action gate — policy, quote, explicit confirmation.
    try:
        quote = checkout_service.prepare_checkout(db, session.id, actor="sim",
                                                  actor_type="ai_agent")
    except checkout_service.CheckoutError:
        db.commit()
        return

    try:
        payment = checkout_service.confirm_and_create_payment(
            db, quote["quote_id"], confirmed=True, confirmed_by="sim-buyer",
            actor_type="human",
        )
    except checkout_service.CheckoutError:
        db.commit()
        return

    # 5. A real payment attempt against the sandbox, then real verification.
    from ..payments import get_payment_provider
    provider = get_payment_provider()
    outcome = (Outcome.SUCCESS if rng.random() <= PAYMENT_SUCCESS_PROBABILITY
               else Outcome.FAILURE)
    try:
        attempt = provider.attempt_payment(payment["provider_order_id"], outcome)
        checkout_service.verify_payment(
            provider_order_id=attempt["provider_order_id"],
            provider_payment_id=attempt["provider_payment_id"],
            signature=attempt["signature"],
            db=db, actor="sim-buyer", actor_type="human",
        )
    except Exception as exc:
        log.debug("simulated payment ended in %s: %s", outcome, exc)
        db.rollback()
    db.commit()


def _compare(baseline: metrics_service.Metrics, assisted: metrics_service.Metrics,
             sessions_per_arm: int) -> dict:
    def delta(a: float, b: float) -> dict:
        change = b - a
        pct = round(change / a * 100, 2) if a else None
        return {"baseline": a, "ai_assisted": b, "absolute_change": round(change, 2),
                "percent_change": pct}

    sample_note = (
        "Sample size is too small to claim statistical significance. This is a "
        "directional, synthetic comparison only."
        if sessions_per_arm < 200 else
        "Larger sample, but still synthetic: treat as directional, not as evidence "
        "of real-world lift."
    )
    return {
        "conversion_rate_percent": delta(baseline.conversion_rate, assisted.conversion_rate),
        "aov_paise": delta(baseline.aov_paise, assisted.aov_paise),
        "aov_display": {"baseline": format_inr(baseline.aov_paise),
                        "ai_assisted": format_inr(assisted.aov_paise)},
        "revenue_per_session_paise": delta(baseline.revenue_per_session_paise,
                                           assisted.revenue_per_session_paise),
        "revenue_per_session_display": {
            "baseline": format_inr(baseline.revenue_per_session_paise),
            "ai_assisted": format_inr(assisted.revenue_per_session_paise)},
        "gmv_paise": delta(baseline.gmv_paise, assisted.gmv_paise),
        "gmv_display": {"baseline": format_inr(baseline.gmv_paise),
                        "ai_assisted": format_inr(assisted.gmv_paise)},
        "upsell_acceptance_rate_percent": delta(baseline.upsell_acceptance_rate,
                                                assisted.upsell_acceptance_rate),
        "cross_sell_acceptance_rate_percent": delta(baseline.cross_sell_acceptance_rate,
                                                    assisted.cross_sell_acceptance_rate),
        "addon_acceptance_rate_percent": delta(baseline.addon_acceptance_rate,
                                               assisted.addon_acceptance_rate),
        "upsell_revenue_display": {"baseline": format_inr(baseline.upsell_revenue_paise),
                                   "ai_assisted": format_inr(assisted.upsell_revenue_paise)},
        "statistical_note": sample_note,
    }


def list_experiments(db: Session, limit: int = 20) -> list[dict]:
    from sqlalchemy import select
    rows = db.scalars(select(ExperimentRun)
                      .order_by(ExperimentRun.created_at.desc()).limit(limit)).all()
    return [{"id": r.id, "label": r.label, "sessions_per_arm": r.sessions_per_arm,
             "created_at": r.created_at.isoformat(), "results": r.results,
             "is_synthetic": True} for r in rows]


__all__ = ["run_simulation", "list_experiments", "PERSONAS"]
