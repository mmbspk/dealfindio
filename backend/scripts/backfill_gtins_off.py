"""
OFF GTIN recovery: fill in missing barcodes for products by searching Open Food
Facts by name+brand and copying back only *real*, checksum-valid EAN-13 codes.

We never fabricate a barcode. ``search_gtin_by_name_brand`` (ai_dedup) only
returns a ``code`` that OFF actually served AND that passes ``is_ean13_valid``
AND whose brand does not conflict with ours. On top of that this script skips any
code another product already owns (products.gtin is UNIQUE) — a collision is
logged, since it likely IS itself a dedup candidate.

Products are processed high-value first (those with price reports), so the items
users actually see gain their unique barcode soonest. Reuses the polite
``OFTRateLimiter`` (2–5s jitter + 429 backoff) so we stay a good OFF citizen.

Usage:
    python3 scripts/backfill_gtins_off.py --dry-run --limit 20
    python3 scripts/backfill_gtins_off.py --limit 200
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DATABASE_URL", os.environ.get("DATABASE_URL", ""))
os.environ.setdefault("REDIS_URL", "redis://localhost/0")
os.environ.setdefault("SECRET_KEY", "backfill-script")

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.ai_dedup import is_ean13_valid, search_gtin_by_name_brand
from app.services.oft_image_enricher import OFTRateLimiter


async def _run(args) -> None:
    settings = get_settings()
    from app.services.config_store import get_runtime_typed

    rate_limiter = OFTRateLimiter(settings.OFT_SEARCH_MIN_DELAY_S, settings.OFT_SEARCH_MAX_DELAY_S)
    user_agent = settings.OPEN_FOOD_FACTS_USER_AGENT
    timeout = settings.OFT_SEARCH_TIMEOUT_S

    engine = create_engine(settings.DATABASE_URL, echo=False)

    with Session(engine) as session:
        # Resolve the toggle + per-run limit DB-first (admin Configs UI) then env.
        if not get_runtime_typed("OFF_GTIN_BACKFILL_ENABLED", session):
            print(
                "OFF_GTIN_BACKFILL_ENABLED is false — enable it in Admin → Configs (or .env) to run. Aborting."
            )
            sys.exit(1)

        limit = args.limit or get_runtime_typed("OFF_GTIN_BACKFILL_LIMIT_PER_RUN", session)

        # gtin IS NULL, high-value first (has price reports), then most-recent.
        rows = session.execute(
            text(
                """
                SELECT p.id, p.name, p.brand,
                       (SELECT COUNT(*) FROM price_reports pr WHERE pr.product_id = p.id) AS n_prices
                FROM products p
                WHERE p.gtin IS NULL
                ORDER BY n_prices DESC, p.created_at DESC
                LIMIT :limit
            """
            ),
            {"limit": limit},
        ).fetchall()
        print(f"Found {len(rows)} products missing a GTIN (processing up to {limit})")

        filled = 0
        collisions = 0
        no_match = 0

        async with httpx.AsyncClient() as client:
            for r in rows:
                code = await search_gtin_by_name_brand(
                    r.name, r.brand, client, rate_limiter, user_agent, timeout
                )
                if not code or not is_ean13_valid(code):
                    no_match += 1
                    continue

                normalized = code.zfill(14)
                clash = session.execute(
                    text("SELECT id FROM products WHERE gtin = :g LIMIT 1"),
                    {"g": normalized},
                ).fetchone()
                if clash is not None:
                    collisions += 1
                    print(
                        f"  COLLISION  {normalized}  already owned by {clash.id} "
                        f"— likely a dedup candidate: {r.name!r}"
                    )
                    continue

                print(f"  {normalized}  ←  {r.name!r} [{r.brand or '?'}]")
                if not args.dry_run:
                    session.execute(
                        text("UPDATE products SET gtin = :g WHERE id = :id"),
                        {"g": normalized, "id": str(r.id)},
                    )
                    filled += 1
                    if filled % 50 == 0:
                        session.flush()
                else:
                    filled += 1

        if not args.dry_run:
            session.commit()

        prefix = "Dry-run" if args.dry_run else "Done"
        print(
            f"\n{prefix}: {filled} GTINs "
            f"{'would be ' if args.dry_run else ''}recovered, "
            f"{collisions} collisions skipped, {no_match} no match"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover missing GTINs via Open Food Facts")
    parser.add_argument("--dry-run", action="store_true", help="Search + print, write nothing")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max products to attempt (0 = OFF_GTIN_BACKFILL_LIMIT_PER_RUN)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
