"""
Canonical category normalisation for dealfindio.

Raw category strings from Woolworths (e.g. "Fruit & Veg > FRUIT AND VEG") and Coles
(e.g. "Fruit & Vegetables > Fresh Vegetables") are mapped to a consistent set of ~17
human-readable flat names used across the whole app.

Usage:
    from app.services.category_normaliser import normalise_category, CANONICAL_CATEGORIES
    clean = normalise_category("Baby > GENERAL MERCHANDISE")  # → "Baby"
    clean = normalise_category("SomeUnknownThing")            # → None
"""

from __future__ import annotations

# Every known raw category string → canonical name.
# Keys include both the full "root > DEPT" form and bare root forms.
# A value of None means "not a real product category — store as NULL".
#
# INVARIANT: normalise_category(raw) returns only a value in CANONICAL_CATEGORIES or None.
# Unknown retailer categories must NEVER be returned verbatim.
CATEGORY_MAP: dict[str, str | None] = {
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
    # ── Electronics → Home & Garden ──────────────────────────────────────────
    "Electronics": "Home & Garden",
    "Electronics > FRONT OF STORE": "Home & Garden",
    "Electronics > GENERAL MERCHANDISE": "Home & Garden",
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
    # IGA top-level category covering health foods and personal care
    "Health & Dietary": "Health & Beauty",
    # ── Home & Garden ────────────────────────────────────────────────────────
    "Home & Garden": "Home & Garden",
    "Home & Lifestyle": "Home & Garden",
    "Household": "Home & Garden",
    "Home & Lifestyle > GENERAL MERCHANDISE": "Home & Garden",
    "Home & Lifestyle > GROCERIES": "Home & Garden",
    "Home & Lifestyle > FRONT OF STORE": "Home & Garden",
    # ── Lunchbox → Pantry ────────────────────────────────────────────────────
    "Lunchbox": "Pantry",
    "Lunch Box": "Pantry",
    "Lunch Box > GROCERIES": "Pantry",
    "Lunch Box > FRESH CONVENIENCE": "Pantry",
    "Lunch Box > FRUIT AND VEG": "Pantry",
    "Lunch Box > GENERAL MERCHANDISE": "Pantry",
    "Lunch Box > PROPRIETARY BAKERY": "Pantry",
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
    # ── Ready Meals ──────────────────────────────────────────────────────────
    "Ready Meals": "Ready Meals",
    "Dinner": "Ready Meals",
    "Dinner > FRESH CONVENIENCE": "Ready Meals",
    "Dinner > FRUIT AND VEG": "Ready Meals",
    "Dinner > GROCERIES": "Ready Meals",
    "Dinner > DELI SERVICE": "Ready Meals",
    "Dinner > SEAFOOD SERVICE": "Ready Meals",
    # ── Pantry ───────────────────────────────────────────────────────────────
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
    # Drakes
    "Confectionery & Snacks": "Pantry",
    # ── Pet ──────────────────────────────────────────────────────────────────
    "Pet": "Pet",
    "Pets": "Pet",
    "Pet > GROCERIES": "Pet",
    "Pet > FRESH CONVENIENCE": "Pet",
    # Drakes
    "Petcare": "Pet",
    # ── Vitamins & Supplements ───────────────────────────────────────────────
    "Vitamins & Supplements": "Vitamins & Supplements",
    # ── Promotional/layout labels — not real product categories ───────────────
    # These appear as fallback category names in specials crawls or store layouts.
    # Explicit None so normalise_category returns None rather than storing the label.
    "Specials": None,
    "Other": None,
    "Front of Store": None,
    "Container Bonus Credits": None,
    "Christmas": None,
    "General Merchandise": None,
    "Bonus Credit Products": None,
    "Halloween": None,
    "Back to School": None,
    # Unresolved: Coles higher-protein navigation section; no clear canonical fit.
    "Higher Protein Food and Drink": None,
}

# Closed set of valid canonical category values.
# normalise_category() guarantees it returns only a value from this set or None.
CANONICAL_CATEGORIES: frozenset[str] = frozenset(v for v in CATEGORY_MAP.values() if v is not None)


def normalise_category(raw: str | None) -> str | None:
    """
    Map a raw scraper category string to a canonical name, or None.

    Tries exact match first, then strips everything after " > " and tries the root.
    Unknown roots return None — raw retailer strings are never returned verbatim.

    Invariant: result is None or result in CANONICAL_CATEGORIES.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None

    if raw in CATEGORY_MAP:
        return CATEGORY_MAP[raw]

    # Try root only (strip everything after " > ")
    root = raw.split(" > ")[0].strip()
    if root in CATEGORY_MAP:
        return CATEGORY_MAP[root]

    return None
