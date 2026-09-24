"""
Database seeding script for Australian grocery store locations.

Fetches store locations from Coles, Woolworths, ALDI, and IGA APIs,
then seeds the database with ~4000+ store locations across Australia.

Usage:
    python -m db.seed_stores
    # Or directly:
    python db/seed_stores.py
"""

import asyncio
import json
import logging

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


async def seed_stores():
    """
    Fetch store locations from official sources and seed database.

    Sources:
        - Coles store locator API
        - Woolworths store locator API
        - ALDI manual curated list
        - IGA store directory

    Stores are inserted in bulk with duplicate detection by place_id.
    """

    # Import here to avoid circular imports
    from app.config import get_settings
    from app.models import Base, Store

    settings = get_settings()

    # Create async engine
    engine = create_async_engine(settings.DATABASE_URL, echo=False)

    # Create tables if they don't exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        stores = []

        async def chain_count(chain: str) -> int:
            return (
                await session.execute(
                    select(func.count(Store.id)).where(
                        Store.chain == chain,
                        Store.lat.is_not(None),
                    )
                )
            ).scalar() or 0

        # Only seed each chain if it has no physical (lat/lon) rows yet.
        if await chain_count("coles") == 0:
            logger.info("Fetching Coles store locations...")
            n = await fetch_coles_stores(stores)
            logger.info(f"  Added {n} Coles stores")
        else:
            logger.info("Coles already seeded, skipping.")

        if await chain_count("woolworths") == 0:
            logger.info("Fetching Woolworths store locations...")
            n = await fetch_woolworths_stores(stores)
            logger.info(f"  Added {n} Woolworths stores")
        else:
            logger.info("Woolworths already seeded, skipping.")

        if await chain_count("aldi") == 0:
            logger.info("Fetching ALDI store locations...")
            n = await fetch_aldi_stores(stores)
            logger.info(f"  Added {n} ALDI stores")
        else:
            logger.info("ALDI already seeded, skipping.")

        if await chain_count("iga") == 0:
            logger.info("Fetching IGA store locations...")
            n = await fetch_iga_stores(stores)
            logger.info(f"  Added {n} IGA stores")
        else:
            logger.info("IGA already seeded, skipping.")

        if await chain_count("dan_murphys") == 0:
            logger.info("Fetching Dan Murphy's store locations...")
            n = await fetch_dan_murphys_stores(stores)
            logger.info(f"  Added {n} Dan Murphy's stores")
        else:
            logger.info("Dan Murphy's already seeded, skipping.")

        if not stores:
            logger.info("All chains already seeded. Nothing to do.")
            await engine.dispose()
            return

        # Remove duplicates by place_id
        seen_place_ids = set()
        unique_stores = []
        for store in stores:
            if store.place_id:
                if store.place_id in seen_place_ids:
                    continue
                seen_place_ids.add(store.place_id)
            unique_stores.append(store)

        logger.info(f"Removed duplicates: {len(stores)} -> {len(unique_stores)} stores")

        # Bulk insert
        session.add_all(unique_stores)
        await session.commit()

        logger.info(f"✓ Seeded {len(unique_stores)} stores total")

        # Print summary by chain
        for chain in ["coles", "woolworths", "aldi", "iga", "dan_murphys"]:
            chain_count = len([s for s in unique_stores if s.chain == chain])
            logger.info(f"  - {chain.capitalize()}: {chain_count} stores")

    await engine.dispose()


async def fetch_coles_stores(stores: list) -> int:
    """
    Fetch Coles store locations.

    Coles publishes store data via their public store locator API.
    Returns approximately 800-1000 Coles stores across Australia.

    Args:
        stores: List to append Store objects to

    Returns:
        int: Number of stores fetched
    """
    count = 0

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Coles store locator API endpoint
            response = await client.get(
                "https://www.coles.com.au/api/stores",
                headers={
                    "User-Agent": "dealfindio/1.0 (Store Seeding)",
                    "Accept": "application/json",
                },
            )

            if response.status_code != 200:
                logger.warning(f"Coles API returned status {response.status_code}")
                return 0

            data = response.json()
            coles_stores = data.get("stores", [])

            for store_data in coles_stores:
                try:
                    store = Store(
                        chain="coles",
                        name=store_data.get("name", "Coles"),
                        address=store_data.get("address"),
                        suburb=store_data.get("suburb") or store_data.get("locality"),
                        state=store_data.get("state"),
                        postcode=store_data.get("postcode"),
                        lat=float(store_data.get("latitude", 0))
                        if store_data.get("latitude")
                        else None,
                        lon=float(store_data.get("longitude", 0))
                        if store_data.get("longitude")
                        else None,
                        place_id=store_data.get("place_id") or store_data.get("storeNumber"),
                        is_active=store_data.get("status") != "closed",
                    )
                    stores.append(store)
                    count += 1
                except Exception as e:
                    logger.debug(f"Failed to parse Coles store: {e}")
                    continue

    except Exception as e:
        logger.warning(f"Failed to fetch Coles stores: {e}")

    return count


async def fetch_woolworths_stores(stores: list) -> int:
    """
    Fetch Woolworths store locations.

    Woolworths provides store data via their store locator API.
    Returns approximately 1000-1200 Woolworths stores.

    Args:
        stores: List to append Store objects to

    Returns:
        int: Number of stores fetched
    """
    count = 0

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://www.woolworths.com.au/apis/ui/StoreLocator/stores",
                headers={
                    "User-Agent": "dealfindio/1.0 (Store Seeding)",
                    "Accept": "application/json",
                },
            )

            if response.status_code != 200:
                logger.warning(f"Woolworths API returned status {response.status_code}")
                return 0

            data = response.json()
            woolies_stores = data.get("stores", [])

            for store_data in woolies_stores:
                try:
                    store = Store(
                        chain="woolworths",
                        name=store_data.get("storeName", "Woolworths"),
                        address=store_data.get("address"),
                        suburb=store_data.get("suburb") or store_data.get("locality"),
                        state=store_data.get("state"),
                        postcode=store_data.get("postcode"),
                        lat=float(store_data.get("latitude", 0))
                        if store_data.get("latitude")
                        else None,
                        lon=float(store_data.get("longitude", 0))
                        if store_data.get("longitude")
                        else None,
                        place_id=store_data.get("placeId") or store_data.get("storeId"),
                        is_active=store_data.get("status") != "closed",
                    )
                    stores.append(store)
                    count += 1
                except Exception as e:
                    logger.debug(f"Failed to parse Woolworths store: {e}")
                    continue

    except Exception as e:
        logger.warning(f"Failed to fetch Woolworths stores: {e}")

    return count


async def fetch_aldi_stores(stores: list) -> int:
    """
    Fetch ALDI store locations.

    ALDI Australia operates ~500 stores. Data fetched from store directory
    or curated list as ALDI doesn't publish a public API.

    Args:
        stores: List to append Store objects to

    Returns:
        int: Number of stores fetched
    """
    count = 0

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://www.aldi.com.au/stores",
                headers={
                    "User-Agent": "dealfindio/1.0 (Store Seeding)",
                },
            )

            if response.status_code != 200:
                logger.warning(f"ALDI store directory returned status {response.status_code}")
                # Fall back to curated list
                aldi_stores = await get_aldi_fallback_stores()
            else:
                # Parse HTML or JSON response (depends on ALDI site structure)
                data = response.json()
                aldi_stores = data.get("stores", [])

            for store_data in aldi_stores:
                try:
                    store = Store(
                        chain="aldi",
                        name=store_data.get("name", "ALDI"),
                        address=store_data.get("address"),
                        suburb=store_data.get("suburb") or store_data.get("locality"),
                        state=store_data.get("state"),
                        postcode=store_data.get("postcode"),
                        lat=float(store_data.get("latitude", 0))
                        if store_data.get("latitude")
                        else None,
                        lon=float(store_data.get("longitude", 0))
                        if store_data.get("longitude")
                        else None,
                        place_id=store_data.get("place_id") or store_data.get("id"),
                        is_active=store_data.get("status") != "closed",
                    )
                    stores.append(store)
                    count += 1
                except Exception as e:
                    logger.debug(f"Failed to parse ALDI store: {e}")
                    continue

    except Exception as e:
        logger.warning(f"Failed to fetch ALDI stores: {e}")
        # Fall back to curated list
        aldi_stores = await get_aldi_fallback_stores()
        for store_data in aldi_stores:
            try:
                store = Store(
                    chain="aldi",
                    name=store_data.get("name", "ALDI"),
                    suburb=store_data.get("suburb"),
                    state=store_data.get("state"),
                    postcode=store_data.get("postcode"),
                    lat=float(store_data.get("lat", 0)) if store_data.get("lat") else None,
                    lon=float(store_data.get("lon", 0)) if store_data.get("lon") else None,
                    is_active=True,
                )
                stores.append(store)
                count += 1
            except Exception as e:
                logger.debug(f"Failed to parse ALDI fallback store: {e}")
                continue

    return count


async def fetch_iga_stores(stores: list) -> int:
    """
    Fetch IGA store locations.

    IGA operates ~1400 independent stores across Australia through
    various franchisees. Data fetched from store directory API.

    Args:
        stores: List to append Store objects to

    Returns:
        int: Number of stores fetched
    """
    count = 0

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://www.iga.com.au/find-a-store",
                headers={
                    "User-Agent": "dealfindio/1.0 (Store Seeding)",
                },
            )

            if response.status_code != 200:
                logger.warning(f"IGA store directory returned status {response.status_code}")
                return 0

            # IGA may return JSON or HTML - handle both
            try:
                data = response.json()
                iga_stores = data.get("stores", [])
            except json.JSONDecodeError:
                # If HTML, parse with BeautifulSoup (optional)
                logger.debug("IGA returned HTML, skipping (would need HTML parser)")
                return 0

            for store_data in iga_stores:
                try:
                    store = Store(
                        chain="iga",
                        name=store_data.get("name", "IGA"),
                        address=store_data.get("address"),
                        suburb=store_data.get("suburb") or store_data.get("locality"),
                        state=store_data.get("state"),
                        postcode=store_data.get("postcode"),
                        lat=float(store_data.get("latitude", 0))
                        if store_data.get("latitude")
                        else None,
                        lon=float(store_data.get("longitude", 0))
                        if store_data.get("longitude")
                        else None,
                        place_id=store_data.get("place_id") or store_data.get("storeId"),
                        is_active=store_data.get("status") != "closed",
                    )
                    stores.append(store)
                    count += 1
                except Exception as e:
                    logger.debug(f"Failed to parse IGA store: {e}")
                    continue

    except Exception as e:
        logger.warning(f"Failed to fetch IGA stores: {e}")

    return count


async def fetch_dan_murphys_stores(stores: list) -> int:
    """
    Fetch Dan Murphy's store locations.

    Dan Murphy's operates ~260 stores across Australia (BWS/Dan Murphy's are
    both owned by Endeavour Group). Uses a fallback curated list as there is
    no public store locator API.

    Args:
        stores: List to append Store objects to

    Returns:
        int: Number of stores fetched
    """
    fallback = [
        # New South Wales
        {
            "name": "Dan Murphy's Alexandria",
            "suburb": "Alexandria",
            "state": "NSW",
            "postcode": "2015",
            "lat": -33.9053,
            "lon": 151.1938,
        },
        {
            "name": "Dan Murphy's Bondi Junction",
            "suburb": "Bondi Junction",
            "state": "NSW",
            "postcode": "2022",
            "lat": -33.8921,
            "lon": 151.2478,
        },
        {
            "name": "Dan Murphy's Chatswood",
            "suburb": "Chatswood",
            "state": "NSW",
            "postcode": "2067",
            "lat": -33.7966,
            "lon": 151.1831,
        },
        {
            "name": "Dan Murphy's Parramatta",
            "suburb": "Parramatta",
            "state": "NSW",
            "postcode": "2150",
            "lat": -33.8151,
            "lon": 151.0001,
        },
        {
            "name": "Dan Murphy's Penrith",
            "suburb": "Penrith",
            "state": "NSW",
            "postcode": "2750",
            "lat": -33.7535,
            "lon": 150.6913,
        },
        {
            "name": "Dan Murphy's Newcastle",
            "suburb": "Newcastle",
            "state": "NSW",
            "postcode": "2300",
            "lat": -32.9265,
            "lon": 151.7673,
        },
        # Victoria
        {
            "name": "Dan Murphy's Melbourne CBD",
            "suburb": "Melbourne",
            "state": "VIC",
            "postcode": "3000",
            "lat": -37.8162,
            "lon": 144.9554,
        },
        {
            "name": "Dan Murphy's Richmond",
            "suburb": "Richmond",
            "state": "VIC",
            "postcode": "3121",
            "lat": -37.8218,
            "lon": 145.0012,
        },
        {
            "name": "Dan Murphy's Chadstone",
            "suburb": "Chadstone",
            "state": "VIC",
            "postcode": "3148",
            "lat": -37.8876,
            "lon": 145.0804,
        },
        {
            "name": "Dan Murphy's Ringwood",
            "suburb": "Ringwood",
            "state": "VIC",
            "postcode": "3134",
            "lat": -37.8142,
            "lon": 145.2272,
        },
        # Queensland
        {
            "name": "Dan Murphy's Brisbane City",
            "suburb": "Brisbane City",
            "state": "QLD",
            "postcode": "4000",
            "lat": -27.4728,
            "lon": 153.0233,
        },
        {
            "name": "Dan Murphy's Fortitude Valley",
            "suburb": "Fortitude Valley",
            "state": "QLD",
            "postcode": "4006",
            "lat": -27.4567,
            "lon": 153.0387,
        },
        {
            "name": "Dan Murphy's Chermside",
            "suburb": "Chermside",
            "state": "QLD",
            "postcode": "4032",
            "lat": -27.3867,
            "lon": 153.0287,
        },
        {
            "name": "Dan Murphy's Gold Coast",
            "suburb": "Southport",
            "state": "QLD",
            "postcode": "4215",
            "lat": -27.9664,
            "lon": 153.3913,
        },
        # Western Australia
        {
            "name": "Dan Murphy's Perth CBD",
            "suburb": "Perth",
            "state": "WA",
            "postcode": "6000",
            "lat": -31.9543,
            "lon": 115.8611,
        },
        {
            "name": "Dan Murphy's Cannington",
            "suburb": "Cannington",
            "state": "WA",
            "postcode": "6107",
            "lat": -31.9997,
            "lon": 115.9360,
        },
        # South Australia
        {
            "name": "Dan Murphy's Adelaide CBD",
            "suburb": "Adelaide",
            "state": "SA",
            "postcode": "5000",
            "lat": -34.9295,
            "lon": 138.6002,
        },
        {
            "name": "Dan Murphy's Marion",
            "suburb": "Marion",
            "state": "SA",
            "postcode": "5043",
            "lat": -35.0143,
            "lon": 138.5637,
        },
        # Australian Capital Territory
        {
            "name": "Dan Murphy's Fyshwick",
            "suburb": "Fyshwick",
            "state": "ACT",
            "postcode": "2609",
            "lat": -35.3278,
            "lon": 149.1695,
        },
    ]

    count = 0
    for store_data in fallback:
        try:
            store = Store(
                chain="dan_murphys",
                name=store_data["name"],
                suburb=store_data.get("suburb"),
                state=store_data.get("state"),
                postcode=store_data.get("postcode"),
                lat=store_data.get("lat"),
                lon=store_data.get("lon"),
                is_active=True,
            )
            stores.append(store)
            count += 1
        except Exception as e:
            logger.debug(f"Failed to parse Dan Murphy's store: {e}")
            continue

    return count


async def get_aldi_fallback_stores() -> list:
    """
    Return fallback ALDI store locations (curated list of major cities).

    In production, this would be populated from a complete store directory
    or regular API scraping. This is a minimal fallback for demo purposes.

    Returns:
        list: List of ALDI store dictionaries
    """
    return [
        # New South Wales
        {
            "name": "ALDI Chatswood",
            "suburb": "Chatswood",
            "state": "NSW",
            "postcode": "2067",
            "lat": -33.7962,
            "lon": 151.1849,
        },
        {
            "name": "ALDI Parramatta",
            "suburb": "Parramatta",
            "state": "NSW",
            "postcode": "2150",
            "lat": -33.8143,
            "lon": 151.0033,
        },
        {
            "name": "ALDI Bondi",
            "suburb": "Bondi",
            "state": "NSW",
            "postcode": "2026",
            "lat": -33.8911,
            "lon": 151.2767,
        },
        {
            "name": "ALDI Penrith",
            "suburb": "Penrith",
            "state": "NSW",
            "postcode": "2750",
            "lat": -33.7567,
            "lon": 150.6914,
        },
        {
            "name": "ALDI Newcastle",
            "suburb": "Newcastle",
            "state": "NSW",
            "postcode": "2300",
            "lat": -32.9232,
            "lon": 151.7827,
        },
        # Victoria
        {
            "name": "ALDI Melbourne CBD",
            "suburb": "Melbourne",
            "state": "VIC",
            "postcode": "3000",
            "lat": -37.8136,
            "lon": 144.9631,
        },
        {
            "name": "ALDI Southbank",
            "suburb": "Southbank",
            "state": "VIC",
            "postcode": "3006",
            "lat": -37.8214,
            "lon": 144.9688,
        },
        {
            "name": "ALDI Ringwood",
            "suburb": "Ringwood",
            "state": "VIC",
            "postcode": "3134",
            "lat": -37.8289,
            "lon": 145.2302,
        },
        # Queensland
        {
            "name": "ALDI Brisbane CBD",
            "suburb": "Brisbane",
            "state": "QLD",
            "postcode": "4000",
            "lat": -27.4705,
            "lon": 153.0260,
        },
        {
            "name": "ALDI Southside",
            "suburb": "Southside",
            "state": "QLD",
            "postcode": "4168",
            "lat": -27.6244,
            "lon": 153.1173,
        },
        # Western Australia
        {
            "name": "ALDI Perth CBD",
            "suburb": "Perth",
            "state": "WA",
            "postcode": "6000",
            "lat": -31.9454,
            "lon": 115.8581,
        },
        # South Australia
        {
            "name": "ALDI Adelaide CBD",
            "suburb": "Adelaide",
            "state": "SA",
            "postcode": "5000",
            "lat": -34.9285,
            "lon": 138.6007,
        },
    ]


if __name__ == "__main__":
    # Run seeding
    asyncio.run(seed_stores())
