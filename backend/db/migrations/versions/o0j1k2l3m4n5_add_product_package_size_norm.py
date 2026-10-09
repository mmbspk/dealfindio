"""add package_size_norm to products

Adds Product.package_size_norm — a canonical normalized size string
(e.g. "375mL", "1.5L", "500g", "24x375mL") derived from the raw
package_size strings supplied by scrapers.

Backfills existing products from their ProductRetailerListing rows using
the canonical selection rule: catalog_last_seen_at DESC NULLS LAST, id ASC,
picking the first listing whose package_size_raw normalizes to a non-None
value.

Revision ID: o0j1k2l3m4n5
Revises: n9i0j1k2l3m4
Create Date: 2026-10-08
"""
from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "o0j1k2l3m4n5"
down_revision = "n9i0j1k2l3m4"
branch_labels = None
depends_on = None

# ── Frozen copy of the normalizer ─────────────────────────────────────────────
# Inlined here so migration replay never depends on the current service state.
# oz intentionally omitted (imperial not supported).

_ML = re.compile(r"millilitres?|milliliters?|mls?", re.I)
_L = re.compile(r"litres?|liters?|(?<!\w)l(?!\w)", re.I)
_G = re.compile(r"grams?|(?<!\w)gm(?!\w)|(?<!\w)g(?!\w)", re.I)
_KG = re.compile(r"kilograms?|kgs?", re.I)

_NUM = r"\d+(?:\.\d+)?"
_UNIT = (
    r"(?:millilitres?|milliliters?|mls?"
    r"|litres?|liters?"
    r"|kilograms?|kgs?"
    r"|grams?|gm"
    r"|[lL](?!\w)"
    r"|[gG](?!\w))"
)

_RE_MULTI = re.compile(rf"(?<![\d.])({_NUM})\s*[xX×]\s*({_NUM})\s*({_UNIT})", re.I)
_RE_SIZE_COUNT = re.compile(
    rf"({_NUM})\s*({_UNIT})\s+(\d+)\s*(?:pack|pk|ct|count|pcs?|pieces?)\b", re.I
)
_RE_SINGLE = re.compile(rf"({_NUM})\s*({_UNIT})", re.I)


def _unit(raw: str) -> str:
    u = raw.strip()
    if _ML.fullmatch(u):
        return "ml"
    if _L.fullmatch(u):
        return "l"
    if _G.fullmatch(u):
        return "g"
    if _KG.fullmatch(u):
        return "kg"
    return ""


def _fmt(v: float) -> str:
    return str(int(v)) if v == int(v) else f"{v:.3f}".rstrip("0").rstrip(".")


def _vol(ml: float) -> str:
    return f"{_fmt(ml)}mL" if ml < 1000 else f"{_fmt(ml / 1000)}L"


def _wt(g: float) -> str:
    return f"{_fmt(g)}g" if g < 1000 else f"{_fmt(g / 1000)}kg"


def _single(v: float, u: str) -> str | None:
    if u in ("ml", "l"):
        ml = v if u == "ml" else v * 1000
        return _vol(ml)
    if u in ("g", "kg"):
        g = v if u == "g" else v * 1000
        return _wt(g)
    return None


def _normalize(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    m = _RE_MULTI.search(s)
    if m:
        count_f, size_val, u = float(m.group(1)), float(m.group(2)), _unit(m.group(3))
        if count_f != int(count_f):
            return None
        count = int(count_f)
        if not u or count == 0 or size_val == 0:
            return None
        inner = _single(size_val, u)
        return f"{count}x{inner}" if inner else None
    m = _RE_SIZE_COUNT.search(s)
    if m:
        size_val, u, count = float(m.group(1)), _unit(m.group(2)), int(m.group(3))
        if not u or count == 0 or size_val == 0:
            return None
        inner = _single(size_val, u)
        return f"{count}x{inner}" if inner else None
    m = _RE_SINGLE.search(s)
    if m:
        size_val, u = float(m.group(1)), _unit(m.group(2))
        if not u or size_val == 0:
            return None
        return _single(size_val, u)
    return None


# ─────────────────────────────────────────────────────────────────────────────


def upgrade() -> None:
    op.add_column("products", sa.Column("package_size_norm", sa.String(50), nullable=True))

    bind = op.get_bind()

    # Count 1: total products
    total_products = bind.execute(sa.text("SELECT COUNT(*) FROM products")).scalar()

    # Fetch all listings with a raw size in canonical selection order:
    # catalog_last_seen_at DESC NULLS LAST, id ASC within each product.
    # Sorting by product_id first groups all of a product's rows together so
    # we can iterate once in Python and pick the first parseable per product.
    rows = bind.execute(
        sa.text(
            """
            SELECT product_id, package_size_raw
            FROM product_retailer_listings
            WHERE package_size_raw IS NOT NULL
            ORDER BY
                product_id,
                catalog_last_seen_at DESC NULLS LAST,
                id ASC
            """
        )
    ).fetchall()

    # Collect distinct products that have at least one raw value
    products_with_raw: set = {str(r[0]) for r in rows}

    # Pick first parseable value per product
    best: dict[str, str] = {}
    for product_id, raw in rows:
        pid = str(product_id)
        if pid in best:
            continue
        norm = _normalize(raw)
        if norm is not None:
            best[pid] = norm

    with_parseable = len(best)

    if best:
        updates = [{"pid": pid, "norm": norm} for pid, norm in best.items()]
        bind.execute(
            sa.text("UPDATE products SET package_size_norm = :norm WHERE id::text = :pid"),
            updates,
        )

    print(
        f"[o0j1k2l3m4n5] package_size_norm backfill: "
        f"total_products={total_products}, "
        f"with_raw={len(products_with_raw)}, "
        f"with_parseable={with_parseable}, "
        f"populated={len(best)}"
    )


def downgrade() -> None:
    op.drop_column("products", "package_size_norm")
