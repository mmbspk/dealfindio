# Story #18 — Dedup Backfill Validation: Phase 1 Findings

**Date:** 2026-10-26  
**Branch:** story/18-dedup-backfill-validation  
**DB used:** dealfindio_dev (dev database, read-only)

---

## Gap 1 — Critical: Both scripts crash on current schema

`Product` has no `external_source` column — it was moved to `ProductRetailerListing` during the
listing-separation migration. Both scripts referenced `Product.external_source`:

```
AttributeError: type object 'Product' has no attribute 'external_source'
```

**Files affected:**
- `backfill_dedup.py` lines 52, 106 — `load_candidates` and `run_backfill`
- `ai_dedup_backfill.py` line 128 — product loading query; line 63 — `_chain_of(p)`

**Fix:** Both scripts now join through `ProductRetailerListing` to resolve chain context.

---

## Gap 2 — Critical: No package-size guard in fuzzy matcher

`FuzzyMatcher.normalize_name` strips all size tokens (`l/ml/kg/g/oz/lb`). This means
"Natural Almonds 750g" and "Natural Almonds 400g" both normalize to "naturalalmonds"
and score 100 against each other — **a false-positive merge**.

### Measured false-positive pairs (score ≥ 92, Phase 1 dry-run probe):

| Chain | Size-conflict pairs | Clean merge candidates |
|-------|---------------------|------------------------|
| ALDI  | **122**             | 151                    |
| Coles | **819**             | 3,186                  |
| IGA   | **1,157**           | 5,182                  |
| **Total** | **2,098**       | **8,519**              |

**Selected false-positive examples (would merge without guard):**

- `Natural Almonds 750g` [750g] → `Natural Almonds` [400g] score=100
- `Iced Coffee Flavoured Milk 2L` [2L] → `Iced Coffee Flavoured Milk` [600mL] score=100
- `Brushed Potatoes 2kg` [2kg] → `Brushed Potatoes` [200g] score=100
- `Lower Carb White Bread 700g` [700g] → `Lower Carb White Bread` [600g] score=100
- `Gluten Free Spirals 500g` [500g] → `Gluten Free Spirals` [600g] score=100
- `Zymil Lactose Free Milk Full Cream` [3L] → `Pauls Zymil Lactose Free Full Cream Milk` [1L] score=95
- `Lactose Free Full Cream Milk` [1L] → `Lactose Free Full Cream Milk 2L` [2L] score=100

**Fix:** Added package-size guard to `backfill_dedup.py` and `ai_dedup_backfill.py`:
```python
if src_size and cand_size and src_size != cand_size:
    # skip — different physical products
    continue
```

Guard is conservative: only fires when BOTH sides have a known `package_size_norm`.
When one side is NULL (not yet enriched), the guard does not block (benefit of doubt).

---

## Gap 3 — IGA not in SOURCE_CHAINS

`backfill_dedup.py` had `SOURCE_CHAINS = ["catalog_aldi", "catalog_coles"]` — IGA's
13,922 products were never checked against Woolworths/Drakes.

**Fix:** IGA added: `SOURCE_CHAINS = ["catalog_aldi", "catalog_coles", "catalog_iga"]`

---

## Gap 4 — merge_into missing propagate_gtin_on_merge

`merge_into` in `backfill_dedup.py` deleted the duplicate product without first
transferring any valid EAN-13 GTIN from the dropped row to the survivor. `ai_dedup.py`
already has `propagate_gtin_on_merge` for exactly this purpose.

**Fix:** `merge_into` now calls `propagate_gtin_on_merge(session, can_id, dup_id)` before
the DELETE.

---

## Gap 5 — ai_dedup_backfill.py passed chain=None to AI judge

`ProductFacts.from_product(pa)` was called without `chain` or `external_id` kwargs.
Both `ProductFacts.chain` fields in every pair were `None`, so `same_chain(a, b)` in
`pre_guard` always returned `False` — the cross-chain veto was silently disabled.

**Fix:** Use `facts_for_product_id(session, str(pa.id))` (existing helper in `ai_dedup.py`)
which resolves chain from the primary `ProductRetailerListing`, same ordering as the
admin dedup UI.

---

## Known conflict products (permanently excluded)

Both products confirmed in DB on 2026-10-26:

| Product ID prefix | Name | Evidence |
|---|---|---|
| `5fda8bda` | Tasmanian Heritage Camembert Cheese | IGA listing: `package_size_raw="125 g"` / Woolworths listing: `package_size_raw="200g"` under same product_id — confirmed bad merge |
| `c24cb38e` | Original Energy Drink 4 Pack 200ml | ALDI listing: `package_size_raw="4 x 250 ml"`, `package_size_norm="4x250mL"` — name/size contradicts raw field |

Both IDs added to `EXCLUDED_PRODUCT_IDS` in both backfill scripts.

---

## Validation of corrected dry-run (2026-10-26)

After applying fixes, ran `backfill_dedup.py --dry-run --chain catalog_aldi`:

```
Processing catalog_aldi (DRY RUN)
============================================================
2673 products checked
162 would merge (clean name matches with same or null size)
134 SKIP (size conflict) — false positives blocked
(dry run — no changes written)
```

**134 false-positive merges blocked for ALDI alone** — the package-size guard is working.

---

## What has NOT been done (Phase 2 prerequisites)

1. AI judge dry-run not executed (requires `AI_DEDUP_ENABLED=true` in Admin → Configs
   and an active Anthropic API key). AI confidence distribution, estimated cost per
   1,000 pairs, and AI false-positive rate are unknown.
2. Coles live validation still blocked by Imperva (Issue #122).
3. No canary write runs executed — Phase 1 is read-only per the story spec.
4. `ProductMergeLog` table not yet created — `_merge_products` has no audit trail.
   This must be done before enabling auto-merge at scale (the badge-sync plan file
   at `.claude/plans/zippy-honking-waffle.md` covers the implementation).

---

## DB state at assessment (2026-10-26)

```
catalog_coles:      24,637 listings
catalog_woolworths: 23,826 listings
catalog_drakes:     14,270 listings
catalog_iga:        13,922 listings
catalog_aldi:        2,673 listings
```

Products linked to multiple chains: 87  
Products with mismatched package_size_raw across chains: 7  
Confirmed bad merges in DB: 1 (5fda8bda Camembert)
