"""Recommendation engine.

    User intent -> structured requirements -> catalog retrieval ->
    candidate ranking -> (LLM explanation) -> recommendation

Requirement extraction and ranking are deterministic and testable. The LLM is
handed an already-ranked shortlist and only chooses phrasing; it cannot add a
product that retrieval did not surface, and it never sees a price it could
restate incorrectly without the backend contradicting it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..domain.money import format_inr, rupees_to_paise
from ..models import Product
from . import catalog

log = logging.getLogger("services.recommend")

#: Use-case vocabulary -> catalog tags. Extending the catalog means extending
#: this map, not retraining anything.
USE_CASE_TAGS: dict[str, tuple[str, ...]] = {
    "programming": ("programming", "developer", "coding", "software"),
    "coding": ("programming", "developer", "coding"),
    "development": ("programming", "developer"),
    "gaming": ("gaming", "gamer", "esports"),
    "game": ("gaming",),
    "travel": ("travel", "portable", "lightweight"),
    "photography": ("photography", "camera", "photo"),
    "photo": ("photography", "camera"),
    "video": ("video", "content-creation", "vlogging"),
    "vlogging": ("vlogging", "video", "content-creation"),
    "student": ("student", "budget", "study"),
    "study": ("student", "study"),
    "office": ("office", "productivity", "business"),
    "work": ("office", "productivity"),
    "business": ("business", "office"),
    "design": ("design", "creative", "colour-accurate"),
    "music": ("music", "audio"),
    "audio": ("audio", "music"),
    "fitness": ("fitness", "sports", "health"),
    "home": ("home", "smart-home"),
}

CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "Laptops": ("laptop", "notebook", "macbook", "ultrabook"),
    "Smartphones": ("phone", "smartphone", "mobile", "handset", "iphone"),
    "Cameras": ("camera", "dslr", "mirrorless", "photography"),
    "Audio": ("headphone", "earphone", "earbud", "speaker", "audio", "headset"),
    # Compound hints are listed first-class because `detect_category` resolves
    # ties by hint length: without "laptop stand" here, "laptop" (6 chars)
    # outranks "stand" (5) and a stand request lands in the laptop aisle.
    "Accessories": ("mouse", "keyboard", "stand", "hub", "dock", "cable",
                    "bag", "tripod", "memory card", "sd card", "case", "charger",
                    "laptop stand", "laptop bag", "laptop backpack", "laptop sleeve",
                    "phone case", "phone charger", "camera bag", "camera strap",
                    "usb-c dock", "usb hub", "mouse pad"),
    "Monitors": ("monitor", "display", "screen"),
    "Wearables": ("watch", "smartwatch", "band", "tracker"),
}


@dataclass
class Requirements:
    """Structured form of what the shopper asked for."""
    raw_query: str = ""
    keywords: list[str] = field(default_factory=list)
    max_price_paise: int | None = None
    min_price_paise: int | None = None
    category: str | None = None
    brands: list[str] = field(default_factory=list)
    use_case_tags: list[str] = field(default_factory=list)
    quantity: int = 1

    def to_dict(self) -> dict:
        return {
            "raw_query": self.raw_query,
            "keywords": self.keywords,
            "max_price_paise": self.max_price_paise,
            "max_price_display": format_inr(self.max_price_paise) if self.max_price_paise else None,
            "min_price_paise": self.min_price_paise,
            "category": self.category,
            "brands": self.brands,
            "use_case_tags": self.use_case_tags,
            "quantity": self.quantity,
        }


# ---------------------------------------------------------------------------
# Requirement extraction
# ---------------------------------------------------------------------------
_PRICE_TOKEN = r"(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*(k|thousand|lakh|lakhs|l|cr)?"
_UNDER = re.compile(r"\b(?:under|below|less than|within|upto|up to|max(?:imum)?|"
                    r"budget of|around|about|no more than|cheaper than)\s*" + _PRICE_TOKEN, re.I)
_OVER = re.compile(r"\b(?:over|above|more than|at least|min(?:imum)?|starting (?:at|from))\s*"
                   + _PRICE_TOKEN, re.I)
_BETWEEN = re.compile(r"\bbetween\s*" + _PRICE_TOKEN + r"\s*(?:and|to|-)\s*" + _PRICE_TOKEN, re.I)
_BARE_PRICE = re.compile(r"(?:₹|rs\.?\s*|inr\s*)([\d,]+(?:\.\d+)?)\s*(k|thousand|lakh|lakhs|l)?", re.I)

_MULTIPLIERS = {"k": 1_000, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000,
                "l": 100_000, "cr": 10_000_000}

_STOPWORDS = {
    "i", "need", "a", "an", "the", "for", "under", "below", "with", "and", "or",
    "me", "my", "want", "looking", "some", "please", "can", "you", "find", "show",
    "get", "buy", "is", "are", "to", "of", "in", "on", "that", "this", "it", "good",
    "best", "recommend", "suggest", "setup", "something", "help", "would", "like",
}


def detect_category(text: str) -> str | None:
    """Pick the category whose hint matches most specifically.

    Word boundaries matter here: a substring match would read "headphones" as
    "phone" and send an audio shopper to the smartphone aisle. Ties are broken
    by hint length, so "laptop stand" resolves on the longer, more specific
    token rather than whichever category happens to be declared first.
    """
    best_category, best_length = None, 0
    for category, hints in CATEGORY_HINTS.items():
        for hint in hints:
            # Allow a trailing plural: "phone" should match "phones".
            if re.search(rf"\b{re.escape(hint)}s?\b", text) and len(hint) > best_length:
                best_category, best_length = category, len(hint)
    return best_category


def _to_paise(raw: str, suffix: str | None) -> int:
    value = float(raw.replace(",", ""))
    if suffix:
        value *= _MULTIPLIERS.get(suffix.lower(), 1)
    return rupees_to_paise(value)


def extract_requirements(query: str, *, known_brands: list[str] | None = None) -> Requirements:
    """Parse a natural-language request into structured constraints."""
    text = (query or "").strip()
    req = Requirements(raw_query=text)
    low = text.lower()

    between = _BETWEEN.search(text)
    if between:
        a = _to_paise(between.group(1), between.group(2))
        b = _to_paise(between.group(3), between.group(4))
        req.min_price_paise, req.max_price_paise = min(a, b), max(a, b)
    else:
        under = _UNDER.search(text)
        if under:
            req.max_price_paise = _to_paise(under.group(1), under.group(2))
        over = _OVER.search(text)
        if over:
            req.min_price_paise = _to_paise(over.group(1), over.group(2))
        if req.max_price_paise is None and req.min_price_paise is None:
            bare = _BARE_PRICE.search(text)
            if bare:
                # A bare "₹80,000" in a shopping request reads as a ceiling.
                req.max_price_paise = _to_paise(bare.group(1), bare.group(2))

    req.category = detect_category(low)

    tags: list[str] = []
    for word, mapped in USE_CASE_TAGS.items():
        if re.search(rf"\b{re.escape(word)}\b", low):
            tags.extend(mapped)
    req.use_case_tags = sorted(set(tags))

    if known_brands:
        req.brands = [b for b in known_brands if b and b.lower() in low]

    qty = re.search(r"\b(\d{1,2})\s*(?:x|units?|pieces?)\b", low)
    if qty:
        req.quantity = max(1, min(int(qty.group(1)), 20))

    req.keywords = [
        w for w in re.split(r"[^a-z0-9]+", low)
        if w and w not in _STOPWORDS and not w.isdigit() and len(w) > 2
    ][:12]
    return req


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
@dataclass
class ScoredProduct:
    product: Product
    score: float
    signals: dict[str, float]
    why: str

    def to_dict(self) -> dict:
        d = catalog.product_to_dict(self.product, include_relations=False)
        d["score"] = round(self.score, 2)
        d["signals"] = {k: round(v, 2) for k, v in self.signals.items()}
        d["why"] = self.why
        return d


#: Weights are explicit so a ranking can always be explained and tuned.
WEIGHTS = {
    "text_relevance": 0.30,
    "price_fit": 0.25,
    "category_fit": 0.15,
    "attribute_fit": 0.15,
    "inventory": 0.05,
    "relationship": 0.10,
}


def rank_candidates(
    db: Session,
    req: Requirements,
    *,
    limit: int = 5,
    cart_product_ids: list[str] | None = None,
) -> list[ScoredProduct]:
    """Retrieve and score catalog candidates against the requirements."""
    raw = catalog.search_products(
        db, req.raw_query,
        category=req.category,
        max_price_paise=req.max_price_paise,
        min_price_paise=req.min_price_paise,
        tags=req.use_case_tags,
        in_stock_only=True,
        limit=40,
    )
    if not raw and req.max_price_paise:
        # Nothing under budget: widen so we can say so honestly and show the
        # nearest options, rather than pretending the catalog is empty.
        raw = catalog.search_products(
            db, req.raw_query, category=req.category, in_stock_only=True, limit=20
        )

    cart_ids = set(cart_product_ids or [])
    related_boost: set[str] = set()
    if cart_ids:
        for p in catalog.get_products_by_ids(db, list(cart_ids)).values():
            related_boost.update(p.related_products or [])
            related_boost.update(p.frequently_bought_together or [])
            related_boost.update(p.compatible_products or [])

    scored: list[ScoredProduct] = []
    for product, text_score in raw:
        if product.id in cart_ids:
            continue
        signals = {
            "text_relevance": min(text_score, 100.0),
            "price_fit": _price_fit(product, req),
            "category_fit": _category_fit(product, req),
            "attribute_fit": _attribute_fit(product, req),
            "inventory": 100.0 if product.inventory >= req.quantity else 0.0,
            "relationship": 100.0 if product.id in related_boost else 0.0,
        }
        total = sum(signals[k] * w for k, w in WEIGHTS.items())
        scored.append(ScoredProduct(product, total, signals,
                                    _explain(product, req, signals)))

    scored.sort(key=lambda s: (-s.score, s.product.price_paise))
    return scored[:limit]


def _price_fit(p: Product, req: Requirements) -> float:
    """Reward the best product the shopper can actually afford.

    Within budget, scoring rises toward the ceiling — someone who says "under
    ₹80,000" usually wants the most capable option under ₹80,000, not the
    cheapest. Over budget falls off sharply and hits zero at +25%.
    """
    if req.max_price_paise is None and req.min_price_paise is None:
        return 60.0
    if req.min_price_paise is not None and p.price_paise < req.min_price_paise:
        return 20.0
    if req.max_price_paise is None:
        return 80.0
    if p.price_paise <= req.max_price_paise:
        ratio = p.price_paise / req.max_price_paise if req.max_price_paise else 0
        return 55.0 + 45.0 * min(ratio, 1.0)
    overshoot = (p.price_paise - req.max_price_paise) / req.max_price_paise
    return max(0.0, 40.0 * (1 - overshoot / 0.25))


def _category_fit(p: Product, req: Requirements) -> float:
    if req.category and p.category == req.category:
        return 100.0
    if req.category:
        return 15.0
    if req.keywords and any(k in p.category.lower() or k in p.subcategory.lower()
                            for k in req.keywords):
        return 80.0
    return 50.0


def _attribute_fit(p: Product, req: Requirements) -> float:
    if not req.use_case_tags and not req.brands:
        return 50.0
    score = 0.0
    tags = {str(t).lower() for t in (p.tags or [])}
    attr_blob = " ".join(f"{k} {v}" for k, v in (p.attributes or {}).items()).lower()

    if req.use_case_tags:
        overlap = tags & set(req.use_case_tags)
        score += 70.0 * min(len(overlap) / max(len(req.use_case_tags), 1) * 2, 1.0)
        if not overlap and any(t in attr_blob for t in req.use_case_tags):
            score += 25.0
    if req.brands:
        score += 30.0 if p.brand in req.brands else 0.0
    elif req.use_case_tags:
        score += 15.0
    return min(score, 100.0)


def _explain(p: Product, req: Requirements, signals: dict[str, float]) -> str:
    """One short, factual clause per candidate — from data, not from a model."""
    bits: list[str] = []
    if req.max_price_paise and p.price_paise <= req.max_price_paise:
        headroom = req.max_price_paise - p.price_paise
        bits.append(f"{format_inr(headroom)} under your budget" if headroom
                    else "exactly at your budget")
    elif req.max_price_paise:
        bits.append(f"{format_inr(p.price_paise - req.max_price_paise)} over budget")

    tags = {str(t).lower() for t in (p.tags or [])}
    matched = sorted(tags & set(req.use_case_tags))
    if matched:
        bits.append("tagged " + ", ".join(matched[:3]))
    if signals.get("relationship", 0) > 0:
        bits.append("pairs with what's already in your cart")
    key_attr = next(iter((p.attributes or {}).items()), None)
    if key_attr and len(bits) < 3:
        bits.append(f"{key_attr[0]}: {key_attr[1]}")
    if p.inventory <= 3:
        bits.append(f"only {p.inventory} left")
    return "; ".join(bits) if bits else "matches your search"


__all__ = ["Requirements", "extract_requirements", "ScoredProduct",
           "rank_candidates", "WEIGHTS", "USE_CASE_TAGS", "CATEGORY_HINTS"]
