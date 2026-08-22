"""Catalog reads, writes and search.

Search is deterministic and runs entirely in the database plus RapidFuzz — no
model is involved in deciding which products exist or what they cost.
"""
from __future__ import annotations

import logging
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..domain.money import format_inr
from ..models import Product

log = logging.getLogger("services.catalog")


def product_to_dict(p: Product, *, include_relations: bool = True) -> dict[str, Any]:
    data = {
        "id": p.id,
        "sku": p.sku,
        "name": p.name,
        "description": p.description,
        "category": p.category,
        "subcategory": p.subcategory,
        "brand": p.brand,
        "price_paise": p.price_paise,
        "price_display": format_inr(p.price_paise),
        "currency": p.currency,
        "inventory": p.inventory,
        "in_stock": p.inventory > 0,
        "attributes": p.attributes or {},
        "tags": p.tags or [],
        "images": p.images or [],
        "active": p.active,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }
    if include_relations:
        data["related_products"] = p.related_products or []
        data["frequently_bought_together"] = p.frequently_bought_together or []
        data["compatible_products"] = p.compatible_products or []
    return data


def get_product(db: Session, product_id: str) -> Product | None:
    return db.get(Product, product_id)


def get_product_by_sku(db: Session, sku: str) -> Product | None:
    return db.scalar(select(Product).where(Product.sku == sku))


def get_products_by_ids(db: Session, ids: list[str]) -> dict[str, Product]:
    if not ids:
        return {}
    rows = db.scalars(select(Product).where(Product.id.in_(ids))).all()
    return {p.id: p for p in rows}


def list_products(
    db: Session,
    *,
    category: str | None = None,
    brand: str | None = None,
    active_only: bool = True,
    in_stock_only: bool = False,
    min_price_paise: int | None = None,
    max_price_paise: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Product], int]:
    stmt = select(Product)
    count_stmt = select(func.count()).select_from(Product)

    conditions = []
    if active_only:
        conditions.append(Product.active.is_(True))
    if in_stock_only:
        conditions.append(Product.inventory > 0)
    if category:
        conditions.append(Product.category == category)
    if brand:
        conditions.append(Product.brand == brand)
    if min_price_paise is not None:
        conditions.append(Product.price_paise >= min_price_paise)
    if max_price_paise is not None:
        conditions.append(Product.price_paise <= max_price_paise)

    for c in conditions:
        stmt = stmt.where(c)
        count_stmt = count_stmt.where(c)

    total = db.scalar(count_stmt) or 0
    rows = db.scalars(
        stmt.order_by(Product.price_paise.asc()).limit(limit).offset(offset)
    ).all()
    return list(rows), total


def categories(db: Session) -> list[dict]:
    rows = db.execute(
        select(Product.category, func.count(Product.id),
               func.min(Product.price_paise), func.max(Product.price_paise))
        .where(Product.active.is_(True))
        .group_by(Product.category)
        .order_by(Product.category)
    ).all()
    return [
        {"category": r[0], "product_count": r[1],
         "min_price_paise": r[2], "max_price_paise": r[3],
         "price_range_display": f"{format_inr(r[2])} – {format_inr(r[3])}"}
        for r in rows if r[0]
    ]


def brands(db: Session) -> list[str]:
    rows = db.scalars(
        select(Product.brand).where(Product.active.is_(True), Product.brand != "")
        .distinct().order_by(Product.brand)
    ).all()
    return list(rows)


def search_products(
    db: Session,
    query: str,
    *,
    category: str | None = None,
    max_price_paise: int | None = None,
    min_price_paise: int | None = None,
    tags: list[str] | None = None,
    in_stock_only: bool = True,
    limit: int = 20,
) -> list[tuple[Product, float]]:
    """Two-stage retrieval: SQL narrows, RapidFuzz ranks.

    The SQL pass keeps the candidate set small so this stays fast on a large
    catalog; nothing beyond the shortlist is ever loaded or sent to a model.
    """
    stmt = select(Product).where(Product.active.is_(True))
    if in_stock_only:
        stmt = stmt.where(Product.inventory > 0)
    if category:
        stmt = stmt.where(Product.category == category)
    if max_price_paise is not None:
        stmt = stmt.where(Product.price_paise <= max_price_paise)
    if min_price_paise is not None:
        stmt = stmt.where(Product.price_paise >= min_price_paise)

    terms = [t for t in _tokenize(query) if len(t) > 2]
    if terms:
        like_clauses = []
        for term in terms[:8]:
            pattern = f"%{term}%"
            like_clauses.extend([
                Product.name.ilike(pattern),
                Product.description.ilike(pattern),
                Product.category.ilike(pattern),
                Product.subcategory.ilike(pattern),
                Product.brand.ilike(pattern),
            ])
        # Keep the lexical shortlist, but union it with the price/category
        # filtered set so a vague query still returns something rankable.
        lexical = db.scalars(stmt.where(or_(*like_clauses)).limit(400)).all()
        fallback = db.scalars(stmt.limit(200)).all() if len(lexical) < 5 else []
        pool = {p.id: p for p in [*lexical, *fallback]}.values()
    else:
        pool = db.scalars(stmt.limit(300)).all()

    haystack_query = " ".join(terms) or (query or "").strip().lower()
    scored: list[tuple[Product, float]] = []
    tagset = {t.lower() for t in (tags or [])}

    for p in pool:
        haystack = " ".join([
            p.name, p.brand, p.category, p.subcategory,
            " ".join(str(t) for t in (p.tags or [])),
            (p.description or "")[:300],
        ]).lower()
        if haystack_query:
            score = max(
                fuzz.token_set_ratio(haystack_query, p.name.lower()),
                fuzz.token_set_ratio(haystack_query, haystack) * 0.85,
                fuzz.partial_ratio(haystack_query, haystack) * 0.7,
            )
        else:
            score = 50.0
        if tagset:
            overlap = tagset & {str(t).lower() for t in (p.tags or [])}
            score += 8 * len(overlap)
        scored.append((p, float(score)))

    scored.sort(key=lambda x: (-x[1], x[0].price_paise))
    return scored[:limit]


def _tokenize(text: str) -> list[str]:
    import re
    return [t for t in re.split(r"[^a-z0-9₹]+", (text or "").lower()) if t]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def create_product(db: Session, data: dict) -> Product:
    product = Product(
        sku=data["sku"],
        name=data["name"],
        description=data.get("description", ""),
        category=data.get("category", ""),
        subcategory=data.get("subcategory", ""),
        brand=data.get("brand", ""),
        price_paise=int(data["price_paise"]),
        currency=data.get("currency", "INR"),
        inventory=int(data.get("inventory", 0)),
        attributes=data.get("attributes") or {},
        tags=data.get("tags") or [],
        images=data.get("images") or [],
        related_products=data.get("related_products") or [],
        frequently_bought_together=data.get("frequently_bought_together") or [],
        compatible_products=data.get("compatible_products") or [],
        active=bool(data.get("active", True)),
    )
    db.add(product)
    db.flush()
    return product


_UPDATABLE = {
    "name", "description", "category", "subcategory", "brand", "price_paise",
    "currency", "inventory", "attributes", "tags", "images", "active",
    "related_products", "frequently_bought_together", "compatible_products", "sku",
}


def update_product(db: Session, product: Product, data: dict) -> Product:
    for key, value in data.items():
        if key in _UPDATABLE and value is not None:
            setattr(product, key, value)
    db.flush()
    return product


def delete_product(db: Session, product: Product) -> None:
    """Soft delete — orders reference products, so rows are never removed."""
    product.active = False
    product.inventory = 0
    db.flush()


__all__ = [
    "product_to_dict", "get_product", "get_product_by_sku", "get_products_by_ids",
    "list_products", "categories", "brands", "search_products",
    "create_product", "update_product", "delete_product",
]
