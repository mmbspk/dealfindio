"""
Product search service — Phase C: geo re-ranking + sale magnitude boost + unit-normalised queries.

Full pipeline:
  1. Trigram lane  — word_similarity on unit-normalised query (typo-tolerant, GIN-indexed)
                     with negation-modifier penalty (e.g. "dairy free milk" must not promote
                     "Dairy Milk" over "Dairy Free Milk")
  2. Semantic lane — pgvector cosine similarity (intent queries), weighted higher for multi-word
                     queries via asymmetric RRF k-factor
  3. RRF fusion    — Reciprocal Rank Fusion merges both lists (asymmetric k when query is intent)
  4. Score enrichment (in Python, one DB round-trip):
       a. Sale magnitude — continuous boost proportional to sale_pct_off
       b. Geo proximity  — when lat/lon provided, products at nearby stores score higher

Fallback chain:
  - Non-PostgreSQL (SQLite/tests): ILIKE only
  - No embeddings yet:             trigram lane only
  - No lat/lon:                    no geo signal
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import Session

from app.models import PriceReport, Product, ProductRetailerListing, Store

_TRGM_THRESHOLD = 0.15  # word_similarity floor — permissive for grocery name noise
_CANDIDATE_POOL = 150  # candidates per lane before RRF fusion
_RRF_K = 60  # standard RRF constant (Cormack et al. 2009)

# Asymmetric RRF: when the query has 2+ meaningful words (intent query), we trust the
# semantic lane more by using a larger k for the trigram lane — each trigram rank is worth less.
_RRF_K_TRGM_INTENT = 120  # used for trigram lane when query has 2+ non-stopwords

# Geo decay: at this distance (km) the geo bonus is halved.
_GEO_HALF_LIFE_KM = 5.0

# Score bonus weights (additive on top of RRF score ~0.01–0.03)
_SALE_BONUS_MAX = 0.008  # applied at 100% off, linear interpolation
_GEO_BONUS_MAX = 0.006  # applied at distance=0

# Negation patterns: "X free" means "without X"
# Map negator → the word it negates in the product name.
# e.g. "dairy free" → penalise products containing "dairy" without "free" nearby.
_NEGATION_PENALTY = 0.2  # trigram score multiplied by this when negation mismatch
_NEGATION_STOPWORDS = {"a", "an", "the", "and", "or", "of", "with", "in", "for", "to"}
_NEGATION_MODIFIERS = {"free", "without", "no", "non"}


def _parse_negations(query_words: list[str]) -> list[str]:
    """
    Return words that are negated in the query.

    "dairy free milk"  → ["dairy"]  (dairy is negated by "free")
    "gluten free bread" → ["gluten"]
    "no added sugar"   → ["sugar"]   (sugar negated by "no")
    "without lactose"  → ["lactose"]
    """
    negated: list[str] = []
    for i, w in enumerate(query_words):
        if w in _NEGATION_MODIFIERS:
            if i > 0 and query_words[i - 1] not in _NEGATION_STOPWORDS:
                negated.append(query_words[i - 1])
            if i < len(query_words) - 1 and query_words[i + 1] not in _NEGATION_STOPWORDS:
                negated.append(query_words[i + 1])
    return list(set(negated))


def _negation_score_multiplier(product_name: str, negated_words: list[str]) -> float:
    """
    Return 1.0 if no conflict, _NEGATION_PENALTY if product contains a negated word
    without also containing the negation modifier near it.

    "Dairy Milk Freddo" against negated=["dairy"]:
      - name contains "dairy" ✓ but does NOT contain "free" → penalty
    "Oat Milk Dairy Free 1L" against negated=["dairy"]:
      - name contains "dairy" AND "free" → no penalty
    """
    if not negated_words:
        return 1.0
    name_lower = product_name.lower()
    for word in negated_words:
        if word in name_lower:
            # Check if any negation modifier also appears in the name
            has_modifier = any(mod in name_lower for mod in _NEGATION_MODIFIERS)
            if not has_modifier:
                return _NEGATION_PENALTY
    return 1.0


def _is_intent_query(query_words: list[str]) -> bool:
    """True when the query has 2+ meaningful words (multi-word intent query)."""
    meaningful = [w for w in query_words if w not in _NEGATION_STOPWORDS and len(w) > 1]
    return len(meaningful) >= 2


def _is_postgres(session: Session) -> bool:
    return session.bind.dialect.name == "postgresql"  # type: ignore[union-attr]


def _on_sale_subquery():
    now = datetime.now(timezone.utc)
    return (
        select(func.max(case((PriceReport.is_on_sale == True, 1), else_=0)))
        .where(
            and_(
                PriceReport.product_id == Product.id,
                or_(PriceReport.valid_to.is_(None), PriceReport.valid_to >= now),
            )
        )
        .correlate(Product)
        .scalar_subquery()
    )


def _base_filters(category, chain, in_stock) -> list:
    f = []
    if category:
        f.append(Product.category == category)
    if chain and in_stock is not None:
        # Combined: chain AND in_stock must be satisfied by the same listing
        if in_stock:
            f.append(
                select(ProductRetailerListing.id)
                .where(
                    ProductRetailerListing.product_id == Product.id,
                    ProductRetailerListing.chain == chain,
                    ProductRetailerListing.in_stock == True,  # noqa: E712
                )
                .exists()
            )
        else:
            # Has a listing for this chain but none are in-stock at that chain
            f.append(
                select(ProductRetailerListing.id)
                .where(
                    ProductRetailerListing.product_id == Product.id,
                    ProductRetailerListing.chain == chain,
                )
                .exists()
            )
            f.append(
                ~select(ProductRetailerListing.id)
                .where(
                    ProductRetailerListing.product_id == Product.id,
                    ProductRetailerListing.chain == chain,
                    ProductRetailerListing.in_stock == True,  # noqa: E712
                )
                .exists()
            )
    elif chain:
        f.append(
            select(ProductRetailerListing.id)
            .where(
                ProductRetailerListing.product_id == Product.id,
                ProductRetailerListing.chain == chain,
            )
            .exists()
        )
    elif in_stock is not None:
        if in_stock:
            f.append(
                select(ProductRetailerListing.id)
                .where(
                    ProductRetailerListing.product_id == Product.id,
                    ProductRetailerListing.in_stock == True,  # noqa: E712
                )
                .exists()
            )
        else:
            f.append(
                ~select(ProductRetailerListing.id)
                .where(
                    ProductRetailerListing.product_id == Product.id,
                    ProductRetailerListing.in_stock == True,  # noqa: E712
                )
                .exists()
            )
    return f


def _rrf_score(rank: int, k: int = _RRF_K) -> float:
    return 1.0 / (k + rank)


def _fuse_rrf(
    trgm_rows: list,
    vec_rows: list,
    intent_query: bool = False,
) -> list[tuple[Product, float]]:
    """
    Reciprocal Rank Fusion over two ranked lists, returns descending by fused score.

    When intent_query=True (multi-word query), the trigram lane uses a larger k so
    the semantic lane dominates — preventing partial brand-name matches from winning.
    """
    trgm_k = _RRF_K_TRGM_INTENT if intent_query else _RRF_K
    scores: dict[str, float] = {}
    products: dict[str, Product] = {}

    for rank, (product, _) in enumerate(trgm_rows):
        pid = str(product.id)
        scores[pid] = scores.get(pid, 0.0) + _rrf_score(rank, k=trgm_k)
        products[pid] = product

    for rank, (product, _) in enumerate(vec_rows):
        pid = str(product.id)
        scores[pid] = scores.get(pid, 0.0) + _rrf_score(rank)
        products[pid] = product

    return sorted(
        [(products[pid], scores[pid]) for pid in scores],
        key=lambda x: x[1],
        reverse=True,
    )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def _geo_bonus(distance_km: float) -> float:
    """Exponential decay: bonus = GEO_BONUS_MAX * 0.5^(d / half_life)."""
    return _GEO_BONUS_MAX * (0.5 ** (distance_km / _GEO_HALF_LIFE_KM))


def _sale_bonus(pct_off: float | None) -> float:
    """Linear: 0 at 0%, _SALE_BONUS_MAX at 100%."""
    if not pct_off:
        return 0.0
    return _SALE_BONUS_MAX * min(pct_off / 100.0, 1.0)


def _trigram_lane(
    session, q: str, filters, negated_words: list[str]
) -> list[tuple[Product, float]]:
    from app.services.embeddings import normalise_product_name

    normalised_q = normalise_product_name(q)

    # Primary score: word_similarity against name (fast, GIN-indexed).
    # Tiebreaker: full trigram similarity against "brand name" string — this separates
    # branded queries (e.g. "huggies nappies size 5") from generic matches because
    # similarity() counts shared trigrams across the whole string, so "huggies" appearing
    # in both the query and the brand field lifts Huggies products above generic "Premium
    # Nappies Size 5" which also matches on "nappies size 5" alone.
    brand_name = func.concat(func.coalesce(Product.brand, ""), " ", Product.name)
    similarity_name = func.word_similarity(normalised_q, Product.name)
    full_sim_brand = func.similarity(func.lower(normalised_q), func.lower(brand_name))
    # Blend: word_sim dominates threshold/recall; full_sim breaks ties within a score band
    similarity_score = similarity_name * literal(0.7) + full_sim_brand * literal(0.3)

    stmt = (
        select(Product, similarity_score.label("score"))
        .where(and_(similarity_name >= _TRGM_THRESHOLD, *filters))
        .order_by(similarity_score.desc(), Product.name)
        .limit(_CANDIDATE_POOL)
    )
    rows = session.execute(stmt).all()

    if not rows:
        stmt = (
            select(Product, literal(0.0).label("score"))
            .where(
                and_(
                    Product.name.ilike(f"%{normalised_q}%")
                    | Product.brand.ilike(f"%{normalised_q}%")
                    | Product.package_size.ilike(f"%{normalised_q}%"),
                    *filters,
                )
            )
            .order_by(Product.name)
            .limit(_CANDIDATE_POOL)
        )
        rows = session.execute(stmt).all()

    result = []
    for row in rows:
        product, raw_score = row[0], float(row[1])
        multiplier = _negation_score_multiplier(product.name, negated_words)
        result.append((product, raw_score * multiplier))

    return result


def _vector_lane(session, q, filters) -> list[tuple[Product, float]]:
    try:
        from pgvector.sqlalchemy import Vector
        from sqlalchemy import cast

        from app.services.embeddings import embed_text

        query_vec = embed_text(q)
        cosine_dist = Product.name_embedding.op("<=>")(cast(query_vec, Vector(384)))
        cosine_sim = (1 - cosine_dist).label("vec_score")

        stmt = (
            select(Product, cosine_sim)
            .where(and_(Product.name_embedding.isnot(None), *filters))
            .order_by(cosine_dist)
            .limit(_CANDIDATE_POOL)
        )
        rows = session.execute(stmt).all()
        return [(row[0], float(row[1])) for row in rows]
    except Exception:
        return []


def _enrich_scores(
    session: Session,
    fused: list[tuple[Product, float]],
    lat: float | None,
    lon: float | None,
    radius_km: float,
) -> list[tuple[Product, float]]:
    """
    Add sale-magnitude and geo-proximity bonuses to fused RRF scores.

    One DB round-trip: fetch best (sale_pct_off, store lat/lon) per product.
    """
    if not fused:
        return fused

    product_ids = [p.id for p, _ in fused]
    now = datetime.now(timezone.utc)

    # Best active price report per product: highest sale_pct_off + nearest store
    rows = session.execute(
        select(
            PriceReport.product_id,
            func.max(PriceReport.sale_pct_off).label("best_pct_off"),
        )
        .where(
            and_(
                PriceReport.product_id.in_(product_ids),
                PriceReport.is_on_sale == True,  # noqa: E712
                or_(PriceReport.valid_to.is_(None), PriceReport.valid_to >= now),
            )
        )
        .group_by(PriceReport.product_id)
    ).all()
    sale_map: dict[str, float] = {str(r.product_id): r.best_pct_off or 0.0 for r in rows}

    geo_map: dict[str, float] = {}
    if lat is not None and lon is not None:
        store_rows = session.execute(
            select(PriceReport.product_id, Store.lat, Store.lon)
            .join(Store, PriceReport.store_id == Store.id)
            .where(
                and_(
                    PriceReport.product_id.in_(product_ids),
                    Store.lat.isnot(None),
                    Store.lon.isnot(None),
                )
            )
        ).all()

        # Keep closest store per product within radius
        for pid, slat, slon in store_rows:
            d = _haversine_km(lat, lon, slat, slon)
            if d <= radius_km:
                key = str(pid)
                if key not in geo_map or d < geo_map[key]:
                    geo_map[key] = d

    enriched = []
    for product, rrf in fused:
        pid = str(product.id)
        score = (
            rrf
            + _sale_bonus(sale_map.get(pid))
            + (_geo_bonus(geo_map[pid]) if pid in geo_map else 0.0)
        )
        enriched.append((product, score))

    enriched.sort(key=lambda x: x[1], reverse=True)
    return enriched


def search_products_ranked(
    session: Session,
    *,
    q: str,
    category: str | None = None,
    chain: str | None = None,
    in_stock: bool | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 25.0,
    limit: int = 48,
    offset: int = 0,
) -> list[Product]:
    """
    Hybrid search with geo + sale enrichment.

    Params:
      q          — free-text query (unit-normalised before trigram matching)
      lat / lon  — user location for geo proximity boost (optional)
      radius_km  — max store distance for geo signal (default 25km)
    """
    filters = _base_filters(category, chain, in_stock)

    if not _is_postgres(session):
        # SQLite (tests): plain ILIKE, no enrichment
        on_sale = _on_sale_subquery()
        stmt = (
            select(Product, literal(0.0).label("score"), on_sale.label("on_sale_flag"))
            .where(and_(Product.name.ilike(f"%{q}%"), *filters))
            .order_by(on_sale.desc(), Product.name)
            .limit(_CANDIDATE_POOL)
        )
        rows = session.execute(stmt).all()
        return [row[0] for row in rows][offset : offset + limit]

    # Parse query intent once — used by both the trigram penalty and the RRF k-factor.
    query_words = [w.lower() for w in q.strip().split() if w]
    negated_words = _parse_negations(query_words)
    intent = _is_intent_query(query_words)

    trgm_results = _trigram_lane(session, q, filters, negated_words)
    vec_results = _vector_lane(session, q, filters)

    fused = (
        _fuse_rrf(trgm_results, vec_results, intent_query=intent)
        if vec_results
        else list(trgm_results)
    )

    # Phase C enrichment: sale magnitude + geo proximity
    fused = _enrich_scores(session, fused, lat, lon, radius_km)

    return [p for p, _ in fused][offset : offset + limit]


def search_products_by_gtin(session: Session, *, gtin: str) -> list[Product]:
    return list(session.execute(select(Product).where(Product.gtin == gtin)).scalars().all())
