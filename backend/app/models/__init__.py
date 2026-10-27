"""
SQLAlchemy ORM models for dealfindio.

Models:
- Store: Physical store locations for grocery chains
- Product: Grocery products with identifiers and metadata
- PriceReport: Individual price observations at stores
- UserProfile/User: User authentication and profile data
- WatchlistList: Named lists of products (e.g. "Weekly shop")
- UserWatchlist: User product watchlist items belonging to a list
- Notification: User alerts and notification log
- PricePrediction: ML-powered price forecasts
- ProductMatchCandidate: Cross-chain dedup review queue
"""

from .app_config import AppConfig
from .base import Base, BaseModel
from .catalog_run import CatalogCrawlRun
from .notification import Notification
from .package_size_remediation_log import PackageSizeRemediationLog
from .prediction import PricePrediction
from .price_report import PriceReport
from .product import Product
from .product_match import ProductMatchCandidate
from .product_merge_log import ProductMergeLog
from .product_retailer_listing import ProductRetailerListing
from .store import Store
from .user import User, UserProfile
from .watchlist import UserWatchlist
from .watchlist_list import WatchlistList

__all__ = [
    "AppConfig",
    "Base",
    "BaseModel",
    "Store",
    "Product",
    "ProductMatchCandidate",
    "ProductMergeLog",
    "ProductRetailerListing",
    "PriceReport",
    "User",
    "UserProfile",
    "WatchlistList",
    "UserWatchlist",
    "Notification",
    "PricePrediction",
    "CatalogCrawlRun",
    "PackageSizeRemediationLog",
]
