"""Repair non-canonical Product.category values.

Before this migration, `normalise_category()` had a verbatim fallback that
returned the raw root string for unknown retailer categories.  This caused
promotional/seasonal labels and unmapped retailer department names to leak
into `products.category`.

Known invalid values found in dev DB (2026-10-09):
  Health & Dietary       1177  → "Health & Beauty"  (IGA top-level category)
  Bonus Credit Products   616  → NULL  (promo label)
  Halloween               291  → NULL  (seasonal layout)
  Back to School          192  → NULL  (seasonal layout)
  > GROCERIES             139  → NULL  (malformed — no root before " > ")
  > LIQUOR                 30  → NULL
  > GENERAL MERCHANDISE    14  → NULL
  > PROPRIETARY BAKERY     13  → NULL
  > FRESH CONVENIENCE       6  → NULL
  Higher Protein Food…      3  → NULL  (unresolved Coles navigation section)
  ─────────────────────────────────────────
  Total invalid           2481

This migration re-normalises every product whose category is not in the
canonical set, using a frozen copy of the updated CATEGORY_MAP.  Valid
canonical categories are left untouched.

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
# This is a snapshot of CATEGORY_MAP as of Story #85.  The migration must
# remain runnable even if the runtime map changes in future.

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


def upgrade() -> None:
    conn = op.get_bind()

    # Fetch all products with a non-null, non-canonical category.
    rows = conn.execute(
        text(
            "SELECT id, category FROM products "
            "WHERE category IS NOT NULL"
        )
    ).fetchall()

    changed = 0
    nulled = 0
    remapped = 0

    for product_id, current_category in rows:
        if current_category in _CANONICAL_SET:
            continue  # already valid — do not touch

        new_category = _frozen_normalise(current_category)
        conn.execute(
            text("UPDATE products SET category = :cat WHERE id = :id"),
            {"cat": new_category, "id": product_id},
        )
        changed += 1
        if new_category is None:
            nulled += 1
        else:
            remapped += 1

    print(
        f"[q2l3m4n5o6p7] category repair: {changed} rows changed "
        f"({remapped} remapped, {nulled} → NULL)"
    )


def downgrade() -> None:
    # Non-reversible data repair — original values are not preserved.
    pass
