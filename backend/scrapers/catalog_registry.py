"""
Catalog scraper registry.

The single place that maps a chain name to its ``CatalogScraper`` subclass.
Adding a new store is a two-line change here plus the store's own module — no
task, route, or config-plumbing edits required.

Scraper classes are imported lazily inside ``_load_registry`` so that importing
this module (e.g. from the Celery app or a route) doesn't pull in heavy optional
deps (Playwright, etc.) until a scraper is actually requested.
"""

from __future__ import annotations

from typing import Optional

from app.config import Settings, get_settings
from scrapers.catalog_base import CatalogScraper, CatalogStoreConfig

# Populated on first use by _load_registry().
_REGISTRY: dict[str, type[CatalogScraper]] = {}


def _load_registry() -> dict[str, type[CatalogScraper]]:
    """Build the chain -> scraper-class map (lazy, cached)."""
    if _REGISTRY:
        return _REGISTRY

    from scrapers.catalog_aldi import ALDICatalogScraper
    from scrapers.catalog_coles import ColesCatalogScraper
    from scrapers.catalog_danmurphys import DanMurphysCatalogScraper
    from scrapers.catalog_drakes import DrakesCatalogScraper
    from scrapers.catalog_iga import IGACatalogScraper
    from scrapers.catalog_woolworths import WoolworthsCatalogScraper

    _REGISTRY.update(
        {
            "coles": ColesCatalogScraper,
            "woolworths": WoolworthsCatalogScraper,
            "aldi": ALDICatalogScraper,
            "iga": IGACatalogScraper,
            "dan_murphys": DanMurphysCatalogScraper,
            "drakes": DrakesCatalogScraper,
        }
    )
    return _REGISTRY


def available_chains() -> list[str]:
    """Return the chains that currently have a catalog scraper implemented."""
    return sorted(_load_registry().keys())


def build_store_config(chain: str, settings: Optional[Settings] = None) -> CatalogStoreConfig:
    """Assemble a ``CatalogStoreConfig`` from settings for ``chain``."""
    settings = settings or get_settings()
    upper = chain.upper()
    return CatalogStoreConfig(
        chain=chain,
        email=getattr(settings, f"{upper}_EMAIL", None),
        password=getattr(settings, f"{upper}_PASSWORD", None),
        min_delay_s=settings.CATALOG_MIN_DELAY_S,
        max_delay_s=settings.CATALOG_MAX_DELAY_S,
        burst_limit=settings.CATALOG_BURST_LIMIT,
        page_size=settings.CATALOG_PAGE_SIZE,
        max_pages_per_category=settings.CATALOG_MAX_PAGES_PER_CATEGORY,
        enabled=getattr(settings, f"CATALOG_{upper}_ENABLED", False),
    )


def get_catalog_scraper(
    chain: str,
    db_session=None,
    redis_client=None,
    settings: Optional[Settings] = None,
) -> CatalogScraper:
    """Instantiate the catalog scraper for ``chain``.

    Raises:
        ValueError: if no scraper is registered for the chain.
    """
    registry = _load_registry()
    scraper_cls = registry.get(chain)
    if scraper_cls is None:
        raise ValueError(
            f"No catalog scraper registered for chain {chain!r}. "
            f"Available: {sorted(registry.keys())}"
        )
    config = build_store_config(chain, settings)
    return scraper_cls(
        chain=chain,
        config=config,
        db_session=db_session,
        redis_client=redis_client,
    )
