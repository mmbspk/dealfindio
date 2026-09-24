"""normalise_categories

Revision ID: b79a2870f343
Revises: b25d25aa6cf7
Create Date: 2026-09-07

One-time cleanup: map all raw Woolworths/Coles category strings to the canonical
set defined in app.services.category_normaliser.  Future ingest uses the normaliser
at write time, so this migration only needs to run once.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "b79a2870f343"
down_revision: str | None = "b25d25aa6cf7"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# Complete mapping: raw DB value → canonical name
# Mirrors CATEGORY_MAP in app/services/category_normaliser.py
_CATEGORY_MAP: dict[str, str] = {
    # Baby
    "Baby > GROCERIES": "Baby",
    "Baby > GENERAL MERCHANDISE": "Baby",
    # Bakery
    "Bakery > BAKEHOUSE": "Bakery",
    "Bakery > PROPRIETARY BAKERY": "Bakery",
    # Beer, Wine & Spirits
    "Beer, Wine & Spirits > LIQUOR": "Beer, Wine & Spirits",
    "Liquorland": "Beer, Wine & Spirits",
    # Cleaning & Laundry
    "Cleaning & Laundry": "Cleaning & Laundry",
    "Cleaning & Maintenance": "Cleaning & Laundry",
    "Cleaning & Maintenance > GROCERIES": "Cleaning & Laundry",
    "Cleaning & Maintenance > GENERAL MERCHANDISE": "Cleaning & Laundry",
    "Cleaning & Maintenance > FRESH CONVENIENCE": "Cleaning & Laundry",
    # Dairy, Eggs & Fridge
    "Dairy, Eggs & Fridge > FRESH CONVENIENCE": "Dairy, Eggs & Fridge",
    "Dairy, Eggs & Fridge > GROCERIES": "Dairy, Eggs & Fridge",
    # Deli & Fresh
    "Deli": "Deli & Fresh",
    "Deli > DELI SERVICE": "Deli & Fresh",
    "Deli > FRESH CONVENIENCE": "Deli & Fresh",
    # Drinks
    "Drinks > GROCERIES": "Drinks",
    "Drinks > FRESH CONVENIENCE": "Drinks",
    # Electronics
    "Electronics > FRONT OF STORE": "Electronics",
    "Electronics > GENERAL MERCHANDISE": "Electronics",
    # Frozen
    "Freezer": "Frozen",
    "Freezer > FRESH CONVENIENCE": "Frozen",
    "Freezer > GROCERIES": "Frozen",
    # Fruit & Vegetables
    "Fruit & Veg > FRUIT AND VEG": "Fruit & Vegetables",
    "Fruit & Veg": "Fruit & Vegetables",
    # Health & Beauty
    "Health & Wellness": "Health & Beauty",
    "Health & Wellness > GROCERIES": "Health & Beauty",
    "Health & Wellness > FRUIT AND VEG": "Health & Beauty",
    "Health & Wellness > FRESH CONVENIENCE": "Health & Beauty",
    "Beauty": "Health & Beauty",
    "Beauty > GROCERIES": "Health & Beauty",
    "Personal Care": "Health & Beauty",
    "Personal Care > GROCERIES": "Health & Beauty",
    # Home & Garden
    "Home & Lifestyle": "Home & Garden",
    "Home & Lifestyle > GENERAL MERCHANDISE": "Home & Garden",
    "Home & Lifestyle > GROCERIES": "Home & Garden",
    "Home & Lifestyle > FRONT OF STORE": "Home & Garden",
    # Lunchbox
    "Lunch Box": "Lunchbox",
    "Lunch Box > GROCERIES": "Lunchbox",
    "Lunch Box > FRESH CONVENIENCE": "Lunchbox",
    "Lunch Box > FRUIT AND VEG": "Lunchbox",
    "Lunch Box > GENERAL MERCHANDISE": "Lunchbox",
    "Lunch Box > PROPRIETARY BAKERY": "Lunchbox",
    # Meat & Seafood
    "Poultry, Meat & Seafood": "Meat & Seafood",
    "Poultry, Meat & Seafood > DELI SERVICE": "Meat & Seafood",
    "Poultry, Meat & Seafood > FRESH CONVENIENCE": "Meat & Seafood",
    "Poultry, Meat & Seafood > SEAFOOD SERVICE": "Meat & Seafood",
    # Meals & Dinner
    "Dinner": "Meals & Dinner",
    "Dinner > FRESH CONVENIENCE": "Meals & Dinner",
    "Dinner > FRUIT AND VEG": "Meals & Dinner",
    "Dinner > GROCERIES": "Meals & Dinner",
    "Dinner > DELI SERVICE": "Meals & Dinner",
    "Dinner > SEAFOOD SERVICE": "Meals & Dinner",
    # Pantry
    "Pantry > GROCERIES": "Pantry",
    "Pantry > FRESH CONVENIENCE": "Pantry",
    "Pantry > FRUIT AND VEG": "Pantry",
    "Chips, Chocolates & Snacks": "Pantry",
    "Snacks & Confectionery": "Pantry",
    "Snacks & Confectionery > GROCERIES": "Pantry",
    "Snacks & Confectionery > FRESH CONVENIENCE": "Pantry",
    "Snacks & Confectionery > FRUIT AND VEG": "Pantry",
    "Dietary & World Foods": "Pantry",
    "International Foods": "Pantry",
    "International Foods > GROCERIES": "Pantry",
    "International Foods > FRESH CONVENIENCE": "Pantry",
    "International Foods > FRUIT AND VEG": "Pantry",
    # Pet
    "Pet > GROCERIES": "Pet",
    "Pet > FRESH CONVENIENCE": "Pet",
}


def upgrade() -> None:
    conn = op.get_bind()
    for raw, canonical in _CATEGORY_MAP.items():
        conn.execute(
            text("UPDATE products SET category = :canonical WHERE category = :raw"),
            {"canonical": canonical, "raw": raw},
        )


def downgrade() -> None:
    # Category normalisation is not reversible (many-to-one mapping).
    pass
