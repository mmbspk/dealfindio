"""
Canonical category normalisation for dealfindio.

Raw category strings from Woolworths (e.g. "Fruit & Veg > FRUIT AND VEG") and Coles
(e.g. "Fruit & Vegetables > Fresh Vegetables") are mapped to a consistent set of ~16
human-readable flat names used across the whole app.

Usage:
    from app.services.category_normaliser import normalise_category
    clean = normalise_category("Baby > GENERAL MERCHANDISE")  # → "Baby"
"""

from __future__ import annotations

# Every known raw category string → canonical name.
# Keys include both the full "root > DEPT" form and bare root forms.
CATEGORY_MAP: dict[str, str] = {
    # ── Baby ─────────────────────────────────────────────────────────────────
    "Baby": "Baby",
    "Baby > GROCERIES": "Baby",
    "Baby > GENERAL MERCHANDISE": "Baby",
    # ── Bakery ───────────────────────────────────────────────────────────────
    "Bakery": "Bakery",
    "Bakery > BAKEHOUSE": "Bakery",
    "Bakery > PROPRIETARY BAKERY": "Bakery",
    # ── Beer, Wine & Spirits ─────────────────────────────────────────────────
    "Beer, Wine & Spirits": "Beer, Wine & Spirits",
    "Beer, Wine & Spirits > LIQUOR": "Beer, Wine & Spirits",
    "Liquorland": "Beer, Wine & Spirits",
    "Liquor": "Beer, Wine & Spirits",
    # Dan Murphy's sub-categories → merged
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
    # ── Cleaning & Laundry ───────────────────────────────────────────────────
    "Cleaning & Laundry": "Cleaning & Laundry",
    "Cleaning & Household": "Cleaning & Laundry",
    "Cleaning & Maintenance": "Cleaning & Laundry",
    "Cleaning & Maintenance > GROCERIES": "Cleaning & Laundry",
    "Cleaning & Maintenance > GENERAL MERCHANDISE": "Cleaning & Laundry",
    "Cleaning & Maintenance > FRESH CONVENIENCE": "Cleaning & Laundry",
    # Drakes
    "Household Cleaning & Needs": "Cleaning & Laundry",
    # ── Dairy, Eggs & Fridge ─────────────────────────────────────────────────
    "Dairy, Eggs & Fridge": "Dairy, Eggs & Fridge",
    "Dairy, Eggs and Fridge": "Dairy, Eggs & Fridge",
    "Dairy, Eggs & Fridge > FRESH CONVENIENCE": "Dairy, Eggs & Fridge",
    "Dairy, Eggs & Fridge > GROCERIES": "Dairy, Eggs & Fridge",
    # ── Deli & Fresh ─────────────────────────────────────────────────────────
    "Deli & Fresh": "Deli & Fresh",
    "Deli & Chilled Meats": "Deli & Fresh",
    "Deli": "Deli & Fresh",
    "Deli > DELI SERVICE": "Deli & Fresh",
    "Deli > FRESH CONVENIENCE": "Deli & Fresh",
    # Drakes
    "Deli & Seafood": "Deli & Fresh",
    # ── Drinks ───────────────────────────────────────────────────────────────
    "Drinks": "Drinks",
    "Drinks > GROCERIES": "Drinks",
    "Drinks > FRESH CONVENIENCE": "Drinks",
    # ── Electronics ──────────────────────────────────────────────────────────
    "Electronics": "Electronics",
    "Electronics > FRONT OF STORE": "Electronics",
    "Electronics > GENERAL MERCHANDISE": "Electronics",
    # ── Frozen ───────────────────────────────────────────────────────────────
    "Frozen": "Frozen",
    "Freezer": "Frozen",
    "Freezer > FRESH CONVENIENCE": "Frozen",
    "Freezer > GROCERIES": "Frozen",
    # ── Fruit & Vegetables ───────────────────────────────────────────────────
    "Fruit & Vegetables": "Fruit & Vegetables",
    "Fruits & Vegetables": "Fruit & Vegetables",
    "Fruit and Vegetable": "Fruit & Vegetables",
    "Fruit and Vegetables": "Fruit & Vegetables",
    "Fruit & Veg": "Fruit & Vegetables",
    "Fruit & Veg > FRUIT AND VEG": "Fruit & Vegetables",
    # ── Health & Beauty ──────────────────────────────────────────────────────
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
    # ── Home & Garden ────────────────────────────────────────────────────────
    "Home & Garden": "Home & Garden",
    "Home & Lifestyle": "Home & Garden",
    "Household": "Home & Garden",
    "Home & Lifestyle > GENERAL MERCHANDISE": "Home & Garden",
    "Home & Lifestyle > GROCERIES": "Home & Garden",
    "Home & Lifestyle > FRONT OF STORE": "Home & Garden",
    # ── Lunchbox ─────────────────────────────────────────────────────────────
    "Lunchbox": "Lunchbox",
    "Lunch Box": "Lunchbox",
    "Lunch Box > GROCERIES": "Lunchbox",
    "Lunch Box > FRESH CONVENIENCE": "Lunchbox",
    "Lunch Box > FRUIT AND VEG": "Lunchbox",
    "Lunch Box > GENERAL MERCHANDISE": "Lunchbox",
    "Lunch Box > PROPRIETARY BAKERY": "Lunchbox",
    # ── Meat & Seafood ───────────────────────────────────────────────────────
    "Meat & Seafood": "Meat & Seafood",
    "Meat, Seafood and Deli": "Meat & Seafood",
    "Meat and Seafood": "Meat & Seafood",
    "Poultry, Meat & Seafood > DELI SERVICE": "Meat & Seafood",
    "Poultry, Meat & Seafood > FRESH CONVENIENCE": "Meat & Seafood",
    "Poultry, Meat & Seafood > SEAFOOD SERVICE": "Meat & Seafood",
    "Poultry, Meat & Seafood": "Meat & Seafood",
    # Drakes
    "Meat": "Meat & Seafood",
    # ── Meals & Dinner ───────────────────────────────────────────────────────
    "Dinner": "Meals & Dinner",
    "Dinner > FRESH CONVENIENCE": "Meals & Dinner",
    "Dinner > FRUIT AND VEG": "Meals & Dinner",
    "Dinner > GROCERIES": "Meals & Dinner",
    "Dinner > DELI SERVICE": "Meals & Dinner",
    "Dinner > SEAFOOD SERVICE": "Meals & Dinner",
    # ── Pantry ───────────────────────────────────────────────────────────────
    "Pantry": "Pantry",
    "Pantry > GROCERIES": "Pantry",
    "Pantry > FRESH CONVENIENCE": "Pantry",
    "Pantry > FRUIT AND VEG": "Pantry",
    "Chips, Chocolates & Snacks": "Pantry",
    "Snacks & Confectionery": "Pantry",
    "Food & Snacks": "Pantry",
    "Limited Time Only": "Pantry",
    "Snacks & Confectionery > GROCERIES": "Pantry",
    "Snacks & Confectionery > FRESH CONVENIENCE": "Pantry",
    "Snacks & Confectionery > FRUIT AND VEG": "Pantry",
    "Dietary & World Foods": "Pantry",
    "International Foods": "Pantry",
    "International Foods > GROCERIES": "Pantry",
    "International Foods > FRESH CONVENIENCE": "Pantry",
    "International Foods > FRUIT AND VEG": "Pantry",
    # Drakes
    "Confectionery & Snacks": "Pantry",
    # ── Pet ──────────────────────────────────────────────────────────────────
    "Pet": "Pet",
    "Pets": "Pet",
    "Pet > GROCERIES": "Pet",
    "Pet > FRESH CONVENIENCE": "Pet",
    # Drakes
    "Petcare": "Pet",
    # ── Other ────────────────────────────────────────────────────────────────
    "Other": "Other",
}


def normalise_category(raw: str | None) -> str | None:
    """
    Map a raw scraper category string to a canonical name.

    Tries exact match first, then falls back to stripping the " > SUFFIX" portion
    and matching the root. Unknown categories are returned as their root (no suffix),
    so new scraper categories degrade gracefully rather than silently disappearing.
    """
    if not raw:
        return None
    raw = raw.strip()

    canonical = CATEGORY_MAP.get(raw)
    if canonical:
        return canonical

    # Try root only (strip everything after " > ")
    root = raw.split(" > ")[0].strip()
    return CATEGORY_MAP.get(root, root)
