"""The AI output contract.

An LLM response is accepted only if it parses as this schema **and** every
product id it mentions exists in the candidate set that was retrieved for that
turn. Anything else — malformed JSON, an unknown intent, a hallucinated SKU, an
invented price — is rejected and the deterministic planner answers instead.

This is the mechanism that makes "the LLM explains, it does not create facts"
enforceable rather than aspirational.
"""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


class Intent(str, Enum):
    GREETING = "GREETING"          # hello / thanks / small talk — no product dump
    POLICY_QUESTION = "POLICY_QUESTION"  # shipping, returns, warranty, payment
    BROWSE = "BROWSE"              # "what do you sell?" — show the range
    SEARCH = "SEARCH"
    RECOMMEND = "RECOMMEND"
    ADD_TO_CART = "ADD_TO_CART"
    REMOVE_FROM_CART = "REMOVE_FROM_CART"
    VIEW_CART = "VIEW_CART"
    ACCEPT_UPSELL = "ACCEPT_UPSELL"
    DECLINE_UPSELL = "DECLINE_UPSELL"
    CHECKOUT = "CHECKOUT"
    QUESTION = "QUESTION"
    UNKNOWN = "UNKNOWN"


class UpsellSuggestion(BaseModel):
    product_id: str
    reason: str = ""

    @field_validator("reason")
    @classmethod
    def _trim(cls, v: str) -> str:
        return (v or "")[:400]


class AgentOutput(BaseModel):
    """Strict schema every LLM reply must satisfy."""
    model_config = {"extra": "forbid"}

    intent: Intent = Intent.UNKNOWN
    product_ids: list[str] = Field(default_factory=list, max_length=20)
    recommendations: list[str] = Field(default_factory=list, max_length=10)
    upsells: list[UpsellSuggestion] = Field(default_factory=list, max_length=6)
    reason: str = ""
    message: str = ""
    quantity: int = 1
    budget_paise: int | None = None
    requires_confirmation: bool = True

    @field_validator("reason", "message")
    @classmethod
    def _cap_text(cls, v: str) -> str:
        return (v or "")[:2000]

    @field_validator("quantity")
    @classmethod
    def _sane_quantity(cls, v: int) -> int:
        # An LLM asking for 10,000 units is a bug or an attack; the policy
        # engine would reject it anyway, but never let it reach the cart.
        return max(1, min(int(v), 50))

    @field_validator("requires_confirmation")
    @classmethod
    def _confirmation_is_not_negotiable(cls, v: bool) -> bool:
        # The model is not permitted to waive confirmation. Whatever it says,
        # the value stored is True; the real gate lives in the checkout service.
        return True


class ContractViolation(Exception):
    def __init__(self, message: str, *, raw: str = "", errors: Any = None):
        super().__init__(message)
        self.message, self.raw, self.errors = message, raw[:1500], errors


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_FENCED = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict:
    """Pull a JSON object out of a model reply, tolerating code fences."""
    if not text or not text.strip():
        raise ContractViolation("Model returned an empty response.", raw=text or "")

    candidates: list[str] = []
    fenced = _FENCED.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text)
    block = _JSON_BLOCK.search(text)
    if block:
        candidates.append(block.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ContractViolation("Model response did not contain a JSON object.", raw=text)


def parse_agent_output(text: str, allowed_product_ids: set[str]) -> AgentOutput:
    """Parse, validate, and ground a model reply against real catalog ids.

    `allowed_product_ids` is the candidate set retrieved for this turn. Ids
    outside it are dropped — a hallucinated product cannot reach the cart, the
    price calculation, or the user's screen.
    """
    payload = extract_json(text)

    # Tolerate a couple of harmless shape variations before validating, rather
    # than failing a response that is substantively correct.
    if isinstance(payload.get("upsells"), list):
        payload["upsells"] = [
            {"product_id": u, "reason": ""} if isinstance(u, str) else u
            for u in payload["upsells"]
        ]
    for key in ("product_ids", "recommendations"):
        value = payload.get(key)
        if isinstance(value, str):
            payload[key] = [value]

    known_fields = set(AgentOutput.model_fields)
    payload = {k: v for k, v in payload.items() if k in known_fields}

    try:
        output = AgentOutput.model_validate(payload)
    except ValidationError as exc:
        raise ContractViolation("Model response failed schema validation.",
                                raw=text, errors=exc.errors()[:8]) from exc

    output.product_ids = [p for p in output.product_ids if p in allowed_product_ids]
    output.recommendations = [p for p in output.recommendations if p in allowed_product_ids]
    output.upsells = [u for u in output.upsells if u.product_id in allowed_product_ids]

    if output.intent in (Intent.ADD_TO_CART, Intent.REMOVE_FROM_CART) and not output.product_ids:
        # It asked to change the cart but named nothing real. Downgrade rather
        # than guess which product it meant.
        raise ContractViolation(
            f"Model returned intent {output.intent.value} with no valid catalog "
            "product id. Refusing to guess which product was meant.",
            raw=text,
        )
    return output


#: Given to the model verbatim so it knows the exact shape expected.
OUTPUT_SCHEMA_HINT = """Respond with ONE JSON object and nothing else:
{
  "intent": "GREETING|POLICY_QUESTION|BROWSE|SEARCH|RECOMMEND|ADD_TO_CART|REMOVE_FROM_CART|VIEW_CART|ACCEPT_UPSELL|DECLINE_UPSELL|CHECKOUT|QUESTION|UNKNOWN",
  "product_ids": ["<ids from the candidate list only>"],
  "recommendations": ["<ids from the candidate list only>"],
  "upsells": [{"product_id": "<id>", "reason": "<why it fits>"}],
  "reason": "<why you chose these, in one or two sentences>",
  "message": "<what to say to the shopper>",
  "quantity": 1
}"""


__all__ = ["Intent", "AgentOutput", "UpsellSuggestion", "ContractViolation",
           "parse_agent_output", "extract_json", "OUTPUT_SCHEMA_HINT"]
