"""Deterministic provider — the always-available fallback.

This is not a stub that fakes an LLM. It is a rule-based planner that reads the
same structured planning context the real model reads (candidates already
retrieved and ranked by `services/recommend.py`, the live cart, the merchant's
policy) and emits a contract-valid decision plus a templated explanation.

Because the products, prices and ranking all come from the backend, the
recommendations it produces are exactly as *factually* correct as the LLM's —
the model only ever contributes phrasing. That is why the store stays fully
usable with no model installed.
"""
from __future__ import annotations

import json
import re
import time

from .contract import Intent
from .provider import LLMProvider, LLMResponse

PLANNING_BLOCK = re.compile(
    r"<planning-context>\s*(\{.*?\})\s*</planning-context>", re.DOTALL
)

_ADD_PATTERNS = re.compile(
    r"\b(add|buy|take|get|i'?ll take|pick|choose|include|put)\b", re.I)
_BEST_PATTERNS = re.compile(r"\b(best|top|recommended|first|that one|it)\b", re.I)

#: Small talk. Matched only against short messages so "hi-res camera" is safe.
_GREETING_PATTERNS = re.compile(
    r"^\s*(hi|hey|hello|yo|namaste|good\s+(morning|afternoon|evening)|"
    r"how are you|what'?s up|sup)\b", re.I)
_THANKS_PATTERNS = re.compile(
    r"^\s*(thanks|thank you|thx|ty|cheers|bye|goodbye|see you|ok thanks|nice|cool|great)\b",
    re.I)
#: Questions about the merchant, not about a product.
_POLICY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("shipping", re.compile(r"\b(shipping|delivery|deliver|ship|courier|dispatch|"
                            r"how long.{0,20}(take|arrive))\b", re.I)),
    ("returns", re.compile(r"\b(return|returns|refund|exchange|money back)\b", re.I)),
    ("warranty", re.compile(r"\b(warranty|guarantee|repair|service centre|service center)\b", re.I)),
    ("cancellation", re.compile(r"\b(cancel|cancellation)\b", re.I)),
    ("agent_purchases", re.compile(r"\b(agent|automated buyer|bot).{0,20}(buy|purchase|order)\b", re.I)),
]
_PAYMENT_QUESTION = re.compile(
    r"\b(payment method|how (can|do) i pay|which cards?|upi|net ?banking|cod|"
    r"cash on delivery|do you accept)\b", re.I)
#: "What do you sell?" — show the range rather than guessing a product.
_BROWSE_PATTERNS = re.compile(
    r"\b(what do you (sell|have|stock|offer)|what'?s (available|in stock)|"
    r"show me everything|browse|catalog|catalogue|categories|what kind of)\b", re.I)
_YES_PATTERNS = re.compile(
    r"^\s*(yes|yeah|yep|sure|ok|okay|please do|go ahead|sounds good|add them|"
    r"add it|do it|both|all of them)\b", re.I)
_NO_PATTERNS = re.compile(
    r"^\s*(no|nope|nah|skip|not now|no thanks|don'?t)\b", re.I)
_CHECKOUT_PATTERNS = re.compile(
    r"\b(checkout|check out|pay|purchase now|place (the )?order|complete (my )?order|"
    r"proceed to pay)\b", re.I)
_CART_PATTERNS = re.compile(r"\b(cart|basket|what.{0,10}(do i have|is in)|my items)\b", re.I)
_REMOVE_PATTERNS = re.compile(r"\b(remove|delete|drop|take out|get rid of)\b", re.I)
_QUESTION_PATTERNS = re.compile(
    r"\b(what|why|how|which|does|is it|can it|compare|difference|tell me about)\b", re.I)


class MockProvider(LLMProvider):
    name = "mock"
    model = "deterministic-planner-v1"
    deterministic = True

    def complete(self, *, system: str, user: str, max_tokens: int = 700,
                 temperature: float = 0.2) -> LLMResponse:
        started = time.perf_counter()
        context = self._extract_context(user)
        output = self._plan(context)
        latency = (time.perf_counter() - started) * 1000
        return LLMResponse(text=json.dumps(output), provider=self.name,
                           model=self.model, latency_ms=latency)

    def health(self) -> dict:
        return {"provider": self.name, "available": True, "deterministic": True,
                "model": self.model}

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _extract_context(user: str) -> dict:
        match = PLANNING_BLOCK.search(user or "")
        if not match:
            return {}
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}

    def _plan(self, ctx: dict) -> dict:
        message = str(ctx.get("user_message", ""))
        candidates: list[dict] = ctx.get("candidates", []) or []
        cart: list[dict] = ctx.get("cart", []) or []
        pending: list[dict] = ctx.get("pending_upsells", []) or []
        currency_symbol = "₹"

        def name_of(c: dict) -> str:
            return c.get("name", "this item")

        def price_of(c: dict) -> str:
            return c.get("price_display") or f"{currency_symbol}{c.get('price_paise', 0) // 100:,}"

        # 1. Accepting or declining a pending upsell takes priority: it is a
        #    direct answer to a question the agent just asked.
        if pending and _YES_PATTERNS.search(message):
            ids = [u["product_id"] for u in pending]
            names = ", ".join(u.get("name", u["product_id"]) for u in pending)
            return {
                "intent": Intent.ACCEPT_UPSELL.value,
                "product_ids": ids, "recommendations": [], "upsells": [],
                "reason": "The shopper accepted the suggested add-ons.",
                "message": f"Added {names}. I'll show the updated total.",
                "quantity": 1,
            }
        if pending and _NO_PATTERNS.search(message):
            return {
                "intent": Intent.DECLINE_UPSELL.value,
                "product_ids": [], "recommendations": [], "upsells": [],
                "reason": "The shopper declined the suggested add-ons.",
                "message": "No problem — I've left those out. Your cart is unchanged.",
                "quantity": 1,
            }

        # 1a. Small talk. A shopper saying "hi" or "thanks" wants a reply, not a
        #     product dump — answering everything with a catalog listing is the
        #     single fastest way to make an assistant feel broken.
        short = len(message.split()) <= 6
        if short and _GREETING_PATTERNS.search(message):
            return {
                "intent": Intent.GREETING.value,
                "product_ids": [], "recommendations": [], "upsells": [],
                "reason": "The shopper opened with a greeting.",
                "message": self._greeting_text(ctx),
                "quantity": 1,
            }
        if short and _THANKS_PATTERNS.search(message):
            return {
                "intent": Intent.GREETING.value,
                "product_ids": [], "recommendations": [], "upsells": [],
                "reason": "The shopper acknowledged or signed off.",
                "message": ("Happy to help. Tell me what you're after any time — "
                            "and remember nothing is charged without your approval."),
                "quantity": 1,
            }

        # 1b. Questions about the merchant rather than about a product.
        policies: dict = ctx.get("policies") or {}
        if _PAYMENT_QUESTION.search(message):
            return {
                "intent": Intent.POLICY_QUESTION.value,
                "product_ids": [], "recommendations": [], "upsells": [],
                "reason": "The shopper asked how they can pay.",
                "message": (f"You can pay through {ctx.get('payment_label', 'our checkout')}. "
                            "This store is running in test mode, so no real money moves. "
                            "You'll always see the exact total and approve it before "
                            "anything is charged."),
                "quantity": 1,
            }
        for key, pattern in _POLICY_PATTERNS:
            if pattern.search(message) and key in policies:
                return {
                    "intent": Intent.POLICY_QUESTION.value,
                    "product_ids": [], "recommendations": [], "upsells": [],
                    "reason": f"The shopper asked about the {key} policy.",
                    "message": policies[key],
                    "quantity": 1,
                }

        if _BROWSE_PATTERNS.search(message):
            return {
                "intent": Intent.BROWSE.value,
                "product_ids": [], "recommendations": [], "upsells": [],
                "reason": "The shopper asked what the store carries.",
                "message": self._browse_text(ctx),
                "quantity": 1,
            }

        if _CHECKOUT_PATTERNS.search(message):
            return {
                "intent": Intent.CHECKOUT.value,
                "product_ids": [], "recommendations": [], "upsells": [],
                "reason": "The shopper asked to complete the purchase.",
                "message": ("Let me put together your order summary so you can review "
                            "the exact amount before anything is charged."),
                "quantity": 1,
            }

        if _REMOVE_PATTERNS.search(message):
            if not cart:
                return {
                    "intent": Intent.VIEW_CART.value,
                    "product_ids": [], "recommendations": [], "upsells": [],
                    "reason": "Nothing to remove — the cart is already empty.",
                    "message": "Your cart is already empty, so there's nothing to remove.",
                    "quantity": 1,
                }
            target = self._match_cart_item(message, cart)
            if target:
                return {
                    "intent": Intent.REMOVE_FROM_CART.value,
                    "product_ids": [target["product_id"]],
                    "recommendations": [], "upsells": [],
                    "reason": "The shopper asked to remove an item from the cart.",
                    "message": f"Removed {target.get('name', 'that item')} from your cart.",
                    "quantity": 1,
                }

        if _CART_PATTERNS.search(message) and not _ADD_PATTERNS.search(message):
            return {
                "intent": Intent.VIEW_CART.value,
                "product_ids": [], "recommendations": [], "upsells": [],
                "reason": "The shopper asked what is in their cart.",
                "message": "Here's what's in your cart right now.",
                "quantity": 1,
            }

        if not candidates:
            return {
                "intent": Intent.SEARCH.value,
                "product_ids": [], "recommendations": [], "upsells": [],
                "reason": "No catalog item matched the stated requirements.",
                "message": ("I couldn't find anything in this catalog matching that. "
                            "Try a different budget, category or brand and I'll search again."),
                "quantity": 1,
            }

        top = candidates[0]
        qty = self._extract_quantity(message)

        # 2. An explicit add request.
        if _ADD_PATTERNS.search(message):
            chosen = self._match_candidate(message, candidates) or (
                top if _BEST_PATTERNS.search(message) else None
            )
            if chosen is None and len(candidates) == 1:
                chosen = candidates[0]
            if chosen is not None:
                upsells = [
                    {"product_id": u["product_id"],
                     "reason": u.get("reason", "Frequently bought with this item.")}
                    for u in (ctx.get("upsell_options") or [])[:3]
                ]
                msg = f"Added {name_of(chosen)} — {price_of(chosen)}."
                if upsells:
                    add_on_names = ", ".join(
                        u.get("name", u["product_id"])
                        for u in (ctx.get("upsell_options") or [])[:3])
                    msg += (f" Buyers of this often add {add_on_names}. "
                            "Want me to include them? I'll show the exact extra cost first.")
                return {
                    "intent": Intent.ADD_TO_CART.value,
                    "product_ids": [chosen["id"]],
                    "recommendations": [], "upsells": upsells,
                    "reason": (f"{name_of(chosen)} matched the shopper's stated "
                               f"requirements most closely at {price_of(chosen)}."),
                    "message": msg,
                    "quantity": qty,
                }

        # 3. A question about products already surfaced.
        if _QUESTION_PATTERNS.search(message) and cart:
            return {
                "intent": Intent.QUESTION.value,
                "product_ids": [c["id"] for c in candidates[:3]],
                "recommendations": [], "upsells": [],
                "reason": "The shopper asked about products, not to change the cart.",
                "message": self._comparison_text(candidates[:3]),
                "quantity": 1,
            }

        # 4. Default: recommend from the ranked candidates.
        shortlist = candidates[:3]
        return {
            "intent": Intent.RECOMMEND.value,
            "product_ids": [], "recommendations": [c["id"] for c in shortlist],
            "upsells": [],
            "reason": self._ranking_reason(shortlist, ctx),
            "message": self._recommendation_text(shortlist, ctx),
            "quantity": 1,
        }

    # -- text helpers ------------------------------------------------------
    @staticmethod
    def _extract_quantity(message: str) -> int:
        match = re.search(r"\b(\d{1,2})\s*(x|units?|pieces?|nos?\.?)?\b", message or "")
        if match:
            return max(1, min(int(match.group(1)), 20))
        return 1

    #: Words that appear in almost every product name and so identify nothing.
    _WEAK_NAME_WORDS = {
        "the", "and", "with", "for", "pro", "plus", "max", "mini", "new", "gb",
        "tb", "inch", "wireless", "silent", "portable", "premium", "series",
    }

    @classmethod
    def _match_candidate(cls, message: str, candidates: list[dict]) -> dict | None:
        """Resolve which offered product the shopper means.

        Matching on the *whole* product name only works if the shopper types it
        verbatim, which nobody does — "add mouse" would miss "Kestrel Glide
        Silent Wireless Mouse". So distinctive words are scored instead, with
        an exact-phrase match still winning outright.
        """
        low = f" {(message or '').lower()} "

        # Exact phrase (name, brand or SKU) beats everything.
        best, best_len = None, 0
        for c in candidates:
            for token in (c.get("name", ""), c.get("brand", ""), c.get("sku", "")):
                token = (token or "").lower().strip()
                if len(token) >= 3 and token in low and len(token) > best_len:
                    best, best_len = c, len(token)
        if best:
            return best

        # "Option B" / "the second one".
        match = re.search(r"\boption\s*([abc123])\b", low)
        if match:
            idx = {"a": 0, "b": 1, "c": 2, "1": 0, "2": 1, "3": 2}[match.group(1)]
            if idx < len(candidates):
                return candidates[idx]

        # Otherwise score distinctive words from the name, brand and category.
        scored: list[tuple[float, dict]] = []
        for c in candidates:
            words = set()
            for field in ("name", "brand", "category", "sku"):
                for w in re.split(r"[^a-z0-9]+", str(c.get(field, "")).lower()):
                    if len(w) > 2 and w not in cls._WEAK_NAME_WORDS:
                        words.add(w)
            hits = [w for w in words if f" {w} " in low or f" {w}s " in low]
            if hits:
                scored.append((sum(len(w) for w in hits), c))
        if scored:
            scored.sort(key=lambda x: -x[0])
            return scored[0][1]
        return None

    @staticmethod
    def _match_cart_item(message: str, cart: list[dict]) -> dict | None:
        low = (message or "").lower()
        for item in cart:
            name = (item.get("name") or "").lower()
            if name and (name in low or any(
                    w in low for w in name.split() if len(w) > 4)):
                return item
        return cart[-1] if cart else None

    @staticmethod
    def _greeting_text(ctx: dict) -> str:
        merchant = ctx.get("merchant_name") or "our store"
        cats = ctx.get("categories") or []
        line = (", ".join(cats[:-1]) + " and " + cats[-1]) if len(cats) > 1 else (
            cats[0] if cats else "electronics")
        return (
            f"Hi! I'm the {merchant} shopping assistant. I can search our {line}.\n\n"
            "Tell me what you need and roughly what you want to spend — for example "
            "\"a laptop for programming under ₹80,000\" — and I'll pull real options "
            "from the catalog and explain why each one fits.\n\n"
            "Nothing is ever added or charged without you saying so."
        )

    @staticmethod
    def _browse_text(ctx: dict) -> str:
        cats = ctx.get("category_detail") or []
        if not cats:
            return "Ask me for a category or a budget and I'll search the catalog."
        lines = ["Here's what we carry:"]
        for c in cats:
            lines.append(f"• {c['category']} — {c['product_count']} item(s), "
                         f"{c['price_range_display']}")
        lines.append("\nTell me a category and a budget and I'll narrow it down.")
        return "\n".join(lines)

    @staticmethod
    def _ranking_reason(shortlist: list[dict], ctx: dict) -> str:
        if not shortlist:
            return "No candidates were available."
        budget = ctx.get("budget_display")
        top = shortlist[0]
        bits = [f"{top.get('name')} ranked highest on the catalog's scoring signals"]
        signals = top.get("signals") or {}
        named = [k.replace("_", " ") for k, v in signals.items() if v and v > 0]
        if named:
            bits.append("driven by " + ", ".join(named[:4]))
        if budget:
            bits.append(f"and it fits the stated budget of {budget}")
        return ", ".join(bits) + "."

    @staticmethod
    def _recommendation_text(shortlist: list[dict], ctx: dict) -> str:
        if not shortlist:
            return "I couldn't find a match in this catalog."
        budget = ctx.get("budget_display")
        header = (f"I found {len(shortlist)} option"
                  f"{'s' if len(shortlist) != 1 else ''} in the catalog"
                  + (f" within {budget}" if budget else "") + ":")
        lines = []
        for idx, c in enumerate(shortlist):
            label = chr(ord("A") + idx)
            why = c.get("why") or "matches your stated requirements"
            lines.append(f"Option {label} — {c.get('name')} — "
                         f"{c.get('price_display')} ({why})")
        top = shortlist[0]
        closing = (f"Based on what you described, {top.get('name')} is the best "
                   f"balance. Say \"add the best one\" and I'll put it in your cart — "
                   f"nothing is charged until you approve the total.")
        return header + "\n" + "\n".join(lines) + "\n" + closing

    @staticmethod
    def _comparison_text(candidates: list[dict]) -> str:
        if not candidates:
            return "I don't have those details in the catalog."
        lines = ["Here's how they compare on the catalog's recorded attributes:"]
        for c in candidates:
            attrs = c.get("attributes") or {}
            detail = ", ".join(f"{k}: {v}" for k, v in list(attrs.items())[:4]) or "no attributes recorded"
            lines.append(f"• {c.get('name')} — {c.get('price_display')} — {detail}")
        return "\n".join(lines)


__all__ = ["MockProvider", "PLANNING_BLOCK"]
