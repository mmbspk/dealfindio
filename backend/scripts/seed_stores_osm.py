"""
Seed all Australian store locations from OpenStreetMap via the Overpass API.

Fetches real lat/lon data for Coles, Woolworths, ALDI, IGA and Dan Murphy's.
Safe to re-run — upserts by OSM node/way ID (stored in place_id), so existing
rows are updated rather than duplicated.

Usage:
    cd backend
    poetry run python scripts/seed_stores_osm.py
"""

import logging
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BBOX = "-44,113,-10,154"  # Australia

CHAINS = [
    ("Coles",        "coles"),
    ("Woolworths",   "woolworths"),
    ("ALDI",         "aldi"),
    ("IGA",          "iga"),
    ("Dan Murphy's", "dan_murphys"),
    ("Drakes",       "drakes"),
]

AU_STATE_ABBR = {
    "New South Wales":           "NSW",
    "Victoria":                  "VIC",
    "Queensland":                "QLD",
    "Western Australia":         "WA",
    "South Australia":           "SA",
    "Tasmania":                  "TAS",
    "Australian Capital Territory": "ACT",
    "Northern Territory":        "NT",
}


def fetch_osm(brand: str) -> list[dict]:
    query = (
        f'[out:json][timeout:90];'
        f'(node["brand"="{brand}"]({BBOX});'
        f'way["brand"="{brand}"]({BBOX}););'
        f'out center;'
    )
    for attempt in range(3):
        try:
            r = httpx.post(
                "https://overpass-api.de/api/interpreter",
                content=query,
                headers={"Content-Type": "text/plain", "User-Agent": "dealfindio/1.0"},
                timeout=120,
            )
            r.raise_for_status()
            return r.json().get("elements", [])
        except Exception as e:
            logger.warning(f"Attempt {attempt+1} failed for {brand}: {e}")
            time.sleep(5 * (attempt + 1))
    return []


def parse_element(el: dict, chain_key: str) -> dict | None:
    tags = el.get("tags", {})

    # lat/lon: nodes have them directly, ways have a 'center'
    if el["type"] == "node":
        lat, lon = el.get("lat"), el.get("lon")
    else:
        center = el.get("center", {})
        lat, lon = center.get("lat"), center.get("lon")

    if lat is None or lon is None:
        return None

    brand = tags.get("brand", "").strip()

    full_name  = tags.get("full_name", "").strip()
    branch     = tags.get("branch", "").strip()
    suburb_tag = (tags.get("addr:suburb") or tags.get("addr:city") or "").strip()
    street     = tags.get("addr:street", "").strip()
    postcode   = tags.get("addr:postcode", "").strip()

    if full_name:
        # full_name is already the complete name e.g. "Coles Kurralta Park"
        name = full_name
    elif branch:
        name = f"{brand} {branch}"
    elif suburb_tag:
        name = f"{brand} {suburb_tag}"
    elif street:
        # e.g. "Coles (Anzac Highway)" — better than a bare brand name
        name = f"{brand} ({street})"
    elif postcode:
        name = f"{brand} {postcode}"
    else:
        name = brand

    # Address fields
    addr_parts = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
    ]
    address = " ".join(p for p in addr_parts if p).strip() or None

    suburb   = suburb_tag or None
    postcode = postcode or None

    raw_state = tags.get("addr:state", "").strip()
    state = AU_STATE_ABBR.get(raw_state, raw_state) or None

    place_id = f"osm:{el['type'][0]}{el['id']}"

    return {
        "chain":       chain_key,
        "chain_brand": brand,   # bare brand name — used to detect no-location entries
        "name":        name or brand,
        "address":     address,
        "suburb":      suburb,
        "state":       state,
        "postcode":    postcode,
        "lat":         lat,
        "lon":         lon,
        "place_id":    place_id,
    }


def seed():
    from app.config import get_settings
    from app.models import Store
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL.replace("+asyncpg", ""))

    totals = {"inserted": 0, "updated": 0, "skipped": 0}

    with Session(engine) as session:
        for brand, chain_key in CHAINS:
            logger.info(f"Fetching {brand} from Overpass…")
            elements = fetch_osm(brand)
            logger.info(f"  Got {len(elements)} OSM elements")

            inserted = updated = skipped = 0
            for el in elements:
                parsed = parse_element(el, chain_key)
                if not parsed:
                    skipped += 1
                    continue

                place_id = parsed["place_id"]

                # Skip if name is just the bare brand (no location info in OSM)
                if parsed["name"] == parsed["chain_brand"]:
                    skipped += 1
                    continue

                existing = session.execute(
                    select(Store).where(Store.place_id == place_id)
                ).scalar_one_or_none()

                if existing:
                    # Update coordinates and address in case OSM data improved
                    existing.lat      = parsed["lat"]
                    existing.lon      = parsed["lon"]
                    existing.name     = parsed["name"]
                    existing.address  = parsed["address"]
                    existing.suburb   = parsed["suburb"]
                    existing.state    = parsed["state"]
                    existing.postcode = parsed["postcode"]
                    updated += 1
                else:
                    store = Store(
                        chain=parsed["chain"],
                        name=parsed["name"],
                        address=parsed["address"],
                        suburb=parsed["suburb"],
                        state=parsed["state"],
                        postcode=parsed["postcode"],
                        lat=parsed["lat"],
                        lon=parsed["lon"],
                        place_id=place_id,
                        is_active=True,
                    )
                    session.add(store)
                    inserted += 1

            session.commit()
            logger.info(f"  {brand}: {inserted} inserted, {updated} updated, {skipped} skipped")
            totals["inserted"] += inserted
            totals["updated"]  += updated
            totals["skipped"]  += skipped

            time.sleep(2)  # be polite to Overpass

    logger.info(
        f"Done. Total: {totals['inserted']} inserted, "
        f"{totals['updated']} updated, {totals['skipped']} skipped."
    )
    _dedup(engine)


def _dedup(engine):
    """Remove near-duplicate OSM stores (node + way for same physical location)."""
    from sqlalchemy import text
    with engine.connect() as c:
        deleted = c.execute(text("""
            DELETE FROM stores
            WHERE id IN (
                SELECT b.id
                FROM stores a
                JOIN stores b
                  ON a.chain = b.chain
                 AND a.id < b.id
                 AND a.place_id LIKE 'osm:%'
                 AND b.place_id LIKE 'osm:%'
                 AND (point(a.lon,a.lat) <-> point(b.lon,b.lat)) * 111000 < 150
            )
        """)).rowcount
        c.commit()
    if deleted:
        logger.info(f"Dedup: removed {deleted} near-duplicate OSM stores (<150m, same chain)")


if __name__ == "__main__":
    seed()
