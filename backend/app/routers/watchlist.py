"""
Watchlist router — named lists + items.

Endpoints:
  Lists:
    GET    /watchlists/                       list user's lists (with item count)
    POST   /watchlists/                       create a list
    DELETE /watchlists/{list_id}              delete a list + its items

  Items within a list:
    GET    /watchlists/{list_id}/items        items in a list (with product + price)
    POST   /watchlists/{list_id}/items        add a product to a list
    DELETE /watchlists/{list_id}/items/{product_id}   remove a product from a list
    PATCH  /watchlists/{list_id}/items/{product_id}   update target price

  Cross-list product status (for Save popover):
    GET    /watchlists/product/{product_id}/status   which lists contain this product
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models import PriceReport, Product, Store, User, UserWatchlist, WatchlistList
from app.schemas.watchlist import (
    ProductWatchlistStatus,
    WatchlistItemCreate,
    WatchlistItemResponse,
    WatchlistListCreate,
    WatchlistListResponse,
    WatchlistListUpdate,
)
from app.services.listing_helpers import (
    get_listing_enrichment_by_chain_map,
    get_listing_enrichment_map,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Watchlist"])

# ── helpers ───────────────────────────────────────────────────────────────────


def _require_profile(user: User):
    if not user.profile:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User profile not found")
    return user.profile


def _require_list(list_id: UUID, profile_id: UUID, db: Session) -> WatchlistList:
    lst = db.execute(
        select(WatchlistList).where(
            WatchlistList.id == list_id,
            WatchlistList.user_id == profile_id,
        )
    ).scalar_one_or_none()
    if not lst:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"List {list_id} not found")
    return lst


def _enrich_item(
    item: UserWatchlist,
    db: Session,
    listing_enrichment: dict | None = None,
    chain_listing_enrichment: dict | None = None,
) -> WatchlistItemResponse:
    """Attach product metadata and latest price to a watchlist item.

    listing_enrichment: pre-computed map from get_listing_enrichment_map.
    chain_listing_enrichment: pre-computed map from get_listing_enrichment_by_chain_map.
    When either is None, the helper is called for this single product so callers that
    handle one item at a time (add_item, update_item) still get listing-backed data.
    """
    product = db.get(Product, item.product_id)
    current_price: float | None = None
    current_chain: str | None = None
    is_on_sale = False

    if product:
        if listing_enrichment is None:
            listing_enrichment = get_listing_enrichment_map(db, [product.id])
        if chain_listing_enrichment is None:
            chain_listing_enrichment = get_listing_enrichment_by_chain_map(db, [product.id])
        latest = db.execute(
            select(PriceReport, Store)
            .join(Store, PriceReport.store_id == Store.id)
            .where(
                and_(
                    PriceReport.product_id == item.product_id,
                    or_(
                        PriceReport.valid_to.is_(None),
                        PriceReport.valid_to >= datetime.now(timezone.utc),
                    ),
                )
            )
            .order_by(desc(PriceReport.created_at))
            .limit(1)
        ).first()
        if latest:
            pr, store = latest
            current_price = pr.price_cents / 100
            current_chain = store.chain
            is_on_sale = bool(pr.is_on_sale)

    # Image hierarchy:
    #   1. product.image_url (canonical self-hosted) — always wins
    #   2. current_chain listing image — chain-specific, avoids cross-retailer leakage
    #   3. product-level listing image — fallback when no price / chain unknown
    #   4. product.external_image_url — legacy fallback until #49e drops legacy columns
    # TODO(#49e): remove Product fallback after #49e drops legacy columns
    ld = listing_enrichment.get(product.id) if product and listing_enrichment else None
    chain_ld = (
        chain_listing_enrichment.get((product.id, current_chain))
        if chain_listing_enrichment and current_chain
        else None
    )
    listing_image = (
        (chain_ld["external_image_url"] if chain_ld and chain_ld["external_image_url"] else None)
        or (ld["external_image_url"] if ld and ld["external_image_url"] else None)
        or (product.external_image_url if product else None)
    )

    return WatchlistItemResponse(
        id=item.id,
        user_id=item.user_id,
        list_id=item.list_id,
        product_id=item.product_id,
        product_name=product.name if product else None,
        product_image_url=(product.image_url or listing_image) if product else None,
        product_brand=product.brand if product else None,
        product_category=product.category if product else None,
        current_price_aud=current_price,
        current_chain=current_chain,
        is_on_sale=is_on_sale,
        target_price_cents=item.target_price_cents,
        target_pct_off=item.target_pct_off,
        alert_on_prediction=item.alert_on_prediction,
        preferred_chains=item.preferred_chains,
        is_active=item.is_active,
        created_at=item.created_at,
    )


# ── Lists ─────────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[WatchlistListResponse])
def list_watchlist_lists(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = _require_profile(current_user)
    lists = (
        db.execute(
            select(WatchlistList)
            .where(WatchlistList.user_id == profile.id)
            .order_by(WatchlistList.created_at)
        )
        .scalars()
        .all()
    )

    result = []
    for lst in lists:
        count = (
            db.execute(
                select(UserWatchlist).where(
                    UserWatchlist.list_id == lst.id,
                    UserWatchlist.is_active.is_(True),
                )
            )
            .scalars()
            .all()
        )
        result.append(
            WatchlistListResponse(
                id=lst.id,
                user_id=lst.user_id,
                name=lst.name,
                emoji=lst.emoji,
                channel_override=lst.channel_override,
                item_count=len(count),
                created_at=lst.created_at,
            )
        )
    return result


@router.post("/", response_model=WatchlistListResponse, status_code=201)
def create_watchlist_list(
    body: WatchlistListCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = _require_profile(current_user)
    lst = WatchlistList(
        user_id=profile.id,
        name=body.name,
        emoji=body.emoji,
        channel_override=body.channel_override,
    )
    db.add(lst)
    db.commit()
    db.refresh(lst)
    return WatchlistListResponse(
        id=lst.id,
        user_id=lst.user_id,
        name=lst.name,
        emoji=lst.emoji,
        channel_override=lst.channel_override,
        item_count=0,
        created_at=lst.created_at,
    )


@router.patch("/{list_id}", response_model=WatchlistListResponse)
def update_watchlist_list(
    list_id: UUID,
    body: WatchlistListUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a watchlist list's name, emoji, or channel_override."""
    profile = _require_profile(current_user)
    lst = _require_list(list_id, profile.id, db)
    if "name" in body.model_fields_set:
        if body.name is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="name cannot be null; omit the field to leave it unchanged",
            )
        lst.name = body.name
    if "emoji" in body.model_fields_set:
        lst.emoji = body.emoji
    if "channel_override" in body.model_fields_set:
        lst.channel_override = body.channel_override
    db.commit()
    db.refresh(lst)
    count = (
        db.execute(
            select(UserWatchlist).where(
                UserWatchlist.list_id == lst.id,
                UserWatchlist.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )
    return WatchlistListResponse(
        id=lst.id,
        user_id=lst.user_id,
        name=lst.name,
        emoji=lst.emoji,
        channel_override=lst.channel_override,
        item_count=len(count),
        created_at=lst.created_at,
    )


@router.delete("/{list_id}", status_code=204)
def delete_watchlist_list(
    list_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = _require_profile(current_user)
    lst = _require_list(list_id, profile.id, db)
    db.delete(lst)
    db.commit()


# ── Items ─────────────────────────────────────────────────────────────────────


@router.get("/{list_id}/items", response_model=list[WatchlistItemResponse])
def list_items(
    list_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = _require_profile(current_user)
    _require_list(list_id, profile.id, db)
    items = (
        db.execute(
            select(UserWatchlist)
            .where(
                UserWatchlist.list_id == list_id,
                UserWatchlist.is_active.is_(True),
            )
            .order_by(desc(UserWatchlist.created_at))
        )
        .scalars()
        .all()
    )
    product_ids = [i.product_id for i in items]
    listing_enrichment = get_listing_enrichment_map(db, product_ids)
    chain_listing_enrichment = get_listing_enrichment_by_chain_map(db, product_ids)
    return [
        _enrich_item(
            i,
            db,
            listing_enrichment=listing_enrichment,
            chain_listing_enrichment=chain_listing_enrichment,
        )
        for i in items
    ]


@router.post("/{list_id}/items", response_model=WatchlistItemResponse, status_code=201)
def add_item(
    list_id: UUID,
    body: WatchlistItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = _require_profile(current_user)
    _require_list(list_id, profile.id, db)

    if not db.get(Product, body.product_id):
        raise HTTPException(404, f"Product {body.product_id} not found")

    existing = db.execute(
        select(UserWatchlist).where(
            UserWatchlist.list_id == list_id,
            UserWatchlist.product_id == body.product_id,
        )
    ).scalar_one_or_none()

    if existing:
        if not existing.is_active:
            existing.is_active = True
            db.commit()
            db.refresh(existing)
        return _enrich_item(existing, db)

    item = UserWatchlist(
        user_id=profile.id,
        list_id=list_id,
        product_id=body.product_id,
        target_price_cents=body.target_price_cents,
        target_pct_off=body.target_pct_off,
        alert_on_prediction=body.alert_on_prediction,
        preferred_chains=body.preferred_chains,
        is_active=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _enrich_item(item, db)


@router.delete("/{list_id}/items/{product_id}", status_code=204)
def remove_item(
    list_id: UUID,
    product_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = _require_profile(current_user)
    _require_list(list_id, profile.id, db)
    item = db.execute(
        select(UserWatchlist).where(
            UserWatchlist.list_id == list_id,
            UserWatchlist.product_id == product_id,
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Item not found in list")
    item.is_active = False
    db.commit()


@router.patch("/{list_id}/items/{product_id}", response_model=WatchlistItemResponse)
def update_item(
    list_id: UUID,
    product_id: UUID,
    body: WatchlistItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = _require_profile(current_user)
    _require_list(list_id, profile.id, db)
    item = db.execute(
        select(UserWatchlist).where(
            UserWatchlist.list_id == list_id,
            UserWatchlist.product_id == product_id,
            UserWatchlist.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Item not found in list")
    item.target_price_cents = body.target_price_cents
    item.target_pct_off = body.target_pct_off
    item.alert_on_prediction = body.alert_on_prediction
    item.preferred_chains = body.preferred_chains
    db.commit()
    db.refresh(item)
    return _enrich_item(item, db)


# ── Product status (for Save-to-list popover) ─────────────────────────────────


@router.get("/product/{product_id}/status", response_model=list[ProductWatchlistStatus])
def product_watchlist_status(
    product_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all the user's lists, flagging which ones contain this product."""
    profile = _require_profile(current_user)
    lists = (
        db.execute(
            select(WatchlistList)
            .where(WatchlistList.user_id == profile.id)
            .order_by(WatchlistList.created_at)
        )
        .scalars()
        .all()
    )

    result = []
    for lst in lists:
        item = db.execute(
            select(UserWatchlist).where(
                UserWatchlist.list_id == lst.id,
                UserWatchlist.product_id == product_id,
                UserWatchlist.is_active.is_(True),
            )
        ).scalar_one_or_none()
        result.append(
            ProductWatchlistStatus(
                list_id=lst.id,
                list_name=lst.name,
                list_emoji=lst.emoji,
                in_list=item is not None,
                item_id=item.id if item else None,
            )
        )
    return result
