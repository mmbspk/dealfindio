"""Repair Product.category — listing-derived canonical with legacy fallback.

Before this migration, ``normalise_category()`` had a verbatim fallback that
returned the raw root string for unknown retailer categories.  This caused
promotional/seasonal labels and unmapped retailer department names to leak
into ``products.category``.

This migration applies a two-tier resolution rule per product:

1. **Listing-derived canonical** — ``ProductRetailerListing.external_category``
   ordered by ``catalog_last_seen_at DESC NULLS LAST, id ASC``.  If any
   listing row maps to a canonical value, that wins unconditionally.

2. **Legacy fallback** — if no listing row yields a canonical category (either
   because ``external_category`` is NULL or maps to None), the existing
   ``products.category`` value is normalised through the frozen map.  This
   preserves valid canonical values (e.g. "Pantry" stays "Pantry") while
   clearing or remapping invalid ones (e.g. "Health & Dietary" → "Health &
   Beauty", "Halloween" → NULL).

The fallback is a transitional measure for the period before scrapers have
re-crawled and populated ``external_category``.  Once listing coverage is
complete the runtime ``recompute_product_category()`` becomes authoritative.

NOTE on external_category population
-------------------------------------
As of Story #85, all ``product_retailer_listings.external_category`` rows
were NULL because the column (added in p1k2l3m4n5o6) predates the last full
crawl run.  All five scrapers do extract category and will populate the column
on their next crawl.  Until then, the fallback preserves existing canonical
data so no useful category information is lost.

Revision ID: q2l3m4n5o6p7
Revises: p1k2l3m4n5o6
Create Date: 2026-10-09
"""

from __future__ import annotations

from typing import Optional

from alembic import op
from sqlalchemy import text

revision = "q2l3m4n5o6p7"
down_revision = "p1k2l3m4n5o6"
branch_labels = None
depends_on = None

# ── Frozen category map (self-contained — do not import from app.services) ──
# Snapshot of CATEGORY_MAP as of Story #85.  Must remain runnable even if the
# runtime map changes in future.

_FROZEN_CATEGORY_MAP: dict[str, Optional[str]] = {
    "Baby": "Baby",
    "Baby > GROCERIES": "Baby",
    "Baby > GENERAL MERCHANDISE": "Baby",
    "Bakery": "Bakery",
    "Bakery > BAKEHOUSE": "Bakery",
    "Bakery > PROPRIETARY BAKERY": "Bakery",
    "Beer, Wine & Spirits": "Beer, Wine & Spirits",
    "Beer, Wine & Spirits > LIQUOR": "Beer, Wine & Spirits",
    "Liquorland": "Beer, Wine & Spirits",
    "Liquor": "Beer, Wine & Spirits",
    "Bourbon": "Beer, Wine & Spirits",
    "Brandy & Cognac": "Beer, Wine & Spirits",
    "Champagne & Sparkling": "Beer, Wine & Spirits",
    "Cider": "Beer, Wine & Spirits",
    "Craft Beer": "Beer, Wine & Spirits",
    "Rum": "Beer, Wine & Spirits",
    "Spirits": "Beer, Wine & Spirits",
    "Vodka": "Beer, Wine & Spirits",
    "Whisky": "Beer, Wine & Spirits",
    "White Wine": "Beer, Wine & Spirits",
    "Red Wine": "Beer, Wine & Spirits",
    "Rose Wine": "Beer, Wine & Spirits",
    "Gin": "Beer, Wine & Spirits",
    "Tequila & Mezcal": "Beer, Wine & Spirits",
    "Premix & Ready to Drink": "Beer, Wine & Spirits",
    "Liqueur": "Beer, Wine & Spirits",
    "Cleaning & Laundry": "Cleaning & Laundry",
    "Cleaning & Household": "Cleaning & Laundry",
    "Cleaning & Maintenance": "Cleaning & Laundry",
    "Cleaning & Maintenance > GROCERIES": "Cleaning & Laundry",
    "Cleaning & Maintenance > GENERAL MERCHANDISE": "Cleaning & Laundry",
    "Cleaning & Maintenance > FRESH CONVENIENCE": "Cleaning & Laundry",
    "Household Cleaning & Needs": "Cleaning & Laundry",
    "Dairy, Eggs & Fridge": "Dairy, Eggs & Fridge",
    "Dairy, Eggs and Fridge": "Dairy, Eggs & Fridge",
    "Dairy, Eggs & Fridge > FRESH CONVENIENCE": "Dairy, Eggs & Fridge",
    "Dairy, Eggs & Fridge > GROCERIES": "Dairy, Eggs & Fridge",
    "Deli & Fresh": "Deli & Fresh",
    "Deli & Chilled Meats": "Deli & Fresh",
    "Deli": "Deli & Fresh",
    "Deli > DELI SERVICE": "Deli & Fresh",
    "Deli > FRESH CONVENIENCE": "Deli & Fresh",
    "Deli & Seafood": "Deli & Fresh",
    "Drinks": "Drinks",
    "Drinks > GROCERIES": "Drinks",
    "Drinks > FRESH CONVENIENCE": "Drinks",
    "Electronics": "Home & Garden",
    "Electronics > FRONT OF STORE": "Home & Garden",
    "Electronics > GENERAL MERCHANDISE": "Home & Garden",
    "Frozen": "Frozen",
    "Freezer": "Frozen",
    "Freezer > FRESH CONVENIENCE": "Frozen",
    "Freezer > GROCERIES": "Frozen",
    "Fruit & Vegetables": "Fruit & Vegetables",
    "Fruits & Vegetables": "Fruit & Vegetables",
    "Fruit and Vegetable": "Fruit & Vegetables",
    "Fruit and Vegetables": "Fruit & Vegetables",
    "Fruit & Veg": "Fruit & Vegetables",
    "Fruit & Veg > FRUIT AND VEG": "Fruit & Vegetables",
    "Health & Beauty": "Health & Beauty",
    "Health and Beauty": "Health & Beauty",
    "Health & Wellness": "Health & Beauty",
    "Health & Wellness > GROCERIES": "Health & Beauty",
    "Health & Wellness > FRUIT AND VEG": "Health & Beauty",
    "Health & Wellness > FRESH CONVENIENCE": "Health & Beauty",
    "Beauty": "Health & Beauty",
    "Beauty > GROCERIES": "Health & Beauty",
    "Personal Care": "Health & Beauty",
    "Personal Care > GROCERIES": "Health & Beauty",
    "Health & Dietary": "Health & Beauty",
    "Home & Garden": "Home & Garden",
    "Home & Lifestyle": "Home & Garden",
    "Household": "Home & Garden",
    "Home & Lifestyle > GENERAL MERCHANDISE": "Home & Garden",
    "Home & Lifestyle > GROCERIES": "Home & Garden",
    "Home & Lifestyle > FRONT OF STORE": "Home & Garden",
    "Lunchbox": "Pantry",
    "Lunch Box": "Pantry",
    "Lunch Box > GROCERIES": "Pantry",
    "Lunch Box > FRESH CONVENIENCE": "Pantry",
    "Lunch Box > FRUIT AND VEG": "Pantry",
    "Lunch Box > GENERAL MERCHANDISE": "Pantry",
    "Lunch Box > PROPRIETARY BAKERY": "Pantry",
    "Meat & Seafood": "Meat & Seafood",
    "Meat, Seafood and Deli": "Meat & Seafood",
    "Meat and Seafood": "Meat & Seafood",
    "Poultry, Meat & Seafood > DELI SERVICE": "Meat & Seafood",
    "Poultry, Meat & Seafood > FRESH CONVENIENCE": "Meat & Seafood",
    "Poultry, Meat & Seafood > SEAFOOD SERVICE": "Meat & Seafood",
    "Poultry, Meat & Seafood": "Meat & Seafood",
    "Meat": "Meat & Seafood",
    "Ready Meals": "Ready Meals",
    "Dinner": "Ready Meals",
    "Dinner > FRESH CONVENIENCE": "Ready Meals",
    "Dinner > FRUIT AND VEG": "Ready Meals",
    "Dinner > GROCERIES": "Ready Meals",
    "Dinner > DELI SERVICE": "Ready Meals",
    "Dinner > SEAFOOD SERVICE": "Ready Meals",
    "Pantry": "Pantry",
    "Pantry > GROCERIES": "Pantry",
    "Pantry > FRESH CONVENIENCE": "Pantry",
    "Pantry > FRUIT AND VEG": "Pantry",
    "Chips, Chocolates & Snacks": "Pantry",
    "Snacks & Confectionery": "Pantry",
    "Food & Snacks": "Pantry",
    "Limited Time Only": "Pantry",
    "Super Savers": "Pantry",
    "Lower Prices": "Pantry",
    "Snacks & Confectionery > GROCERIES": "Pantry",
    "Snacks & Confectionery > FRESH CONVENIENCE": "Pantry",
    "Snacks & Confectionery > FRUIT AND VEG": "Pantry",
    "Dietary & World Foods": "Pantry",
    "International Foods": "Pantry",
    "International Foods > GROCERIES": "Pantry",
    "International Foods > FRESH CONVENIENCE": "Pantry",
    "International Foods > FRUIT AND VEG": "Pantry",
    "Confectionery & Snacks": "Pantry",
    "Pet": "Pet",
    "Pets": "Pet",
    "Pet > GROCERIES": "Pet",
    "Pet > FRESH CONVENIENCE": "Pet",
    "Petcare": "Pet",
    "Vitamins & Supplements": "Vitamins & Supplements",
    "Specials": None,
    "Other": None,
    "Front of Store": None,
    "Container Bonus Credits": None,
    "Christmas": None,
    "General Merchandise": None,
    "Bonus Credit Products": None,
    "Halloween": None,
    "Back to School": None,
    "Higher Protein Food and Drink": None,
}

_CANONICAL_SET: frozenset[str] = frozenset(
    v for v in _FROZEN_CATEGORY_MAP.values() if v is not None
)


def _frozen_normalise(raw: Optional[str]) -> Optional[str]:
    """Frozen normaliser — same logic as runtime normalise_category at Story #85."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if raw in _FROZEN_CATEGORY_MAP:
        return _FROZEN_CATEGORY_MAP[raw]
    root = raw.split(" > ")[0].strip()
    if root in _FROZEN_CATEGORY_MAP:
        return _FROZEN_CATEGORY_MAP[root]
    return None


def _apply_fallback_category(
    listing_cat: Optional[str],
    current_cat: Optional[str],
) -> Optional[str]:
    """Two-tier resolution: listing canonical wins; otherwise normalise current.

    Args:
        listing_cat: Best canonical derived from listings for this product,
            or None when no listing row yields a canonical value.
        current_cat: Current products.category value (may be canonical,
            invalid, or NULL).

    Returns:
        The new canonical category, or None.
    """
    if listing_cat is not None:
        return listing_cat
    return _frozen_normalise(current_cat)


def _derive_categories_from_listing_rows(
    rows: list[tuple],
) -> dict:
    """Given listing rows (product_id, external_category) ordered by
    (product_id, catalog_last_seen_at DESC NULLS LAST, id ASC), return
    {product_id: canonical_category} for each product_id that has at least
    one listing whose category maps to a canonical value.

    Products not present in the returned dict have no canonical category
    and should be set to NULL.
    """
    result: dict = {}
    for product_id, raw_cat in rows:
        if product_id in result:
            continue  # already found best canonical for this product
        canonical = _frozen_normalise(raw_cat)
        if canonical is not None:
            result[product_id] = canonical
    return result


def upgrade() -> None:
    conn = op.get_bind()

    # Step 1: Fetch all listing rows with non-NULL external_category.
    # Ordered so the first row per product_id is the best candidate
    # (newest timestamp, lowest id for equal timestamps).
    listing_rows = conn.execute(
        text(
            """
            SELECT product_id, external_category
            FROM product_retailer_listings
            WHERE external_category IS NOT NULL
            ORDER BY product_id,
                     catalog_last_seen_at DESC NULLS LAST,
                     id ASC
            """
        )
    ).fetchall()

    # Step 2: Derive best canonical category per product from listings.
    listing_categories = _derive_categories_from_listing_rows(listing_rows)

    # Step 3: Fetch all products, compute updates.
    product_rows = conn.execute(text("SELECT id, category FROM products")).fetchall()

    updates = []
    changed = 0
    unchanged = 0

    for product_id, current_category in product_rows:
        listing_cat = listing_categories.get(product_id)
        new_category = _apply_fallback_category(listing_cat, current_category)
        if new_category != current_category:
            updates.append({"id": product_id, "cat": new_category})
            changed += 1
        else:
            unchanged += 1

    # Step 4: Apply updates in a single executemany call.
    if updates:
        conn.execute(
            text("UPDATE products SET category = :cat WHERE id = :id"),
            updates,
        )

    # Step 5: Count post-repair state for reporting.
    post_rows = conn.execute(
        text("SELECT category, COUNT(*) FROM products GROUP BY category")
    ).fetchall()
    canonical_count = sum(r[1] for r in post_rows if r[0] in _CANONICAL_SET)
    null_count = sum(r[1] for r in post_rows if r[0] is None)
    invalid_count = sum(r[1] for r in post_rows if r[0] is not None and r[0] not in _CANONICAL_SET)

    print(
        f"[q2l3m4n5o6p7] category repair (listing+fallback): "
        f"{changed} changed, {unchanged} unchanged | "
        f"listing-derived: {len(listing_categories)} | "
        f"post-repair: {canonical_count} canonical, {null_count} NULL, "
        f"{invalid_count} invalid"
    )


def downgrade() -> None:
    # Non-reversible data repair — original values are not preserved.
    pass
