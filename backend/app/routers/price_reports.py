"""
Community price reports router with submission, validation, and moderation endpoints.

Implements REST API for community-sourced price reporting including:
- POST /api/v1/price-reports: Submit new community price report
- GET /api/v1/moderation/reports: Retrieve flagged reports for review (admin)
- POST /api/v1/price-reports/{id}/upvote: Upvote helpful report
- POST /api/v1/price-reports/{id}/dispute: Flag report as inaccurate
- GET /api/v1/users/me: Get current authenticated user profile
- GET /api/v1/users/{user_id}/profile: Get public user profile

Includes geofencing validation, rate limiting, and reputation integration.
"""

import logging
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from redis import Redis
from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from app.deps import get_db, get_redis, get_current_user, verify_firebase_token
from app.models import PriceReport, Product, Store, UserProfile, User
from app.services.geo import haversine_km
from app.schemas.price_report import (
    CommunityPriceReportRequest,
    PriceReportResponse,
    FlaggedReportResponse,
    FlaggedReportsListResponse,
)
from app.schemas.user import UserProfileResponse, UserProfilePublicResponse
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Price Reports"])

# Geofence validation constants
GEOFENCE_RADIUS_METERS = 200


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Distance between two geographic points, in metres.

    Thin wrapper over :func:`app.services.geo.haversine_km` (the single source
    of truth) converting km → m, since the geofence check works in metres.

    Args:
        lat1: First latitude
        lon1: First longitude
        lat2: Second latitude
        lon2: Second longitude

    Returns:
        float: Distance in metres.
    """
    return haversine_km(lat1, lon1, lat2, lon2) * 1000.0


@router.post("/", response_model=PriceReportResponse, status_code=status.HTTP_201_CREATED)
async def create_price_report(
    request: CommunityPriceReportRequest,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> PriceReportResponse:
    """
    Submit a new community price report.

    Validates user authentication, geofence proximity, rate limits, and product lookup.
    Enqueues validation task after creation.

    Request body:
        product_gtin: Product GTIN (optional if product_name provided)
        product_name: Product name (optional if product_gtin provided)
        store_id: UUID of store
        price_cents: Current price in AUD cents
        was_price_cents: Original price before sale (optional)
        latitude: User's current latitude
        longitude: User's current longitude

    Returns:
        PriceReportResponse: Created report with ID and confidence score

    Raises:
        HTTPException 401: Not authenticated
        HTTPException 400: Geofence check failed, rate limit exceeded, invalid input
        HTTPException 404: Product or store not found
        HTTPException 422: Validation error
    """
    logger.info("Processing community price report submission")

    try:
        # Verify authentication
        if not authorization:
            logger.warning("Price report submission without authorization header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        token_data = await verify_firebase_token(authorization)
        firebase_uid = token_data.get("firebase_uid")
        logger.debug(f"Price report from user: {firebase_uid}")

        # Get or create user profile
        user = db.execute(
            select(UserProfile).where(UserProfile.firebase_uid == firebase_uid)
        ).scalar_one_or_none()

        if not user:
            logger.error(f"User profile not found for firebase_uid: {firebase_uid}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found. Please create profile first.",
            )

        # Validate store exists
        store = db.execute(
            select(Store).where(Store.id == request.store_id)
        ).scalar_one_or_none()

        if not store or not store.is_active:
            logger.warning(f"Store not found or inactive: {request.store_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Store {request.store_id} not found or inactive",
            )

        # Geofence validation: user must be within 200m of store
        if store.lat is None or store.lon is None:
            logger.warning(f"Store {store.id} missing coordinates")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Store location data unavailable",
            )

        distance = haversine_distance(
            request.latitude, request.longitude, store.lat, store.lon
        )

        if distance > GEOFENCE_RADIUS_METERS:
            logger.warning(
                f"Geofence check failed for user {user.id}: distance={distance:.0f}m, max={GEOFENCE_RADIUS_METERS}m"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You must be within {GEOFENCE_RADIUS_METERS}m of the store to report prices",
            )

        # Rate limiting: max 20 reports per user per 24h
        rate_limit_key = f"rate_limit:{user.id}:reports"
        current_count = redis.get(rate_limit_key)

        if current_count and int(current_count) >= 20:
            logger.warning(f"Rate limit exceeded for user {user.id}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="You have reached the maximum number of reports (20 per 24 hours)",
            )

        # Product lookup via GTIN or fuzzy name match
        product = None

        if request.product_gtin:
            product = db.execute(
                select(Product).where(Product.gtin == request.product_gtin)
            ).scalar_one_or_none()

        elif request.product_name:
            # Fuzzy match by name
            product = db.execute(
                select(Product).where(Product.name.ilike(f"%{request.product_name}%"))
            ).scalar_one_or_none()

        if not product:
            logger.warning(
                f"Product not found: gtin={request.product_gtin}, name={request.product_name}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found in database",
            )

        # Create price report
        price_report = PriceReport(
            product_id=product.id,
            store_id=store.id,
            price_cents=request.price_cents,
            was_price_cents=request.was_price_cents,
            source="community",
            reporter_user_id=user.id,
            confidence=0.5,  # Initial confidence, will be updated by validator
            is_verified=False,
            is_flagged=False,
            lat=request.latitude,
            lon=request.longitude,
            is_on_sale=request.was_price_cents is not None,
            sale_pct_off=(
                ((request.was_price_cents - request.price_cents) / request.was_price_cents * 100)
                if request.was_price_cents
                else None
            ),
        )

        db.add(price_report)
        db.flush()  # Get ID without committing

        report_id = price_report.id
        logger.info(f"Created price report {report_id} for product {product.id} at store {store.id}")

        # Update rate limit counter
        if current_count:
            redis.incr(rate_limit_key)
        else:
            redis.setex(rate_limit_key, 86400, 1)  # 24 hours

        db.commit()

        # Enqueue validation task
        celery_app.send_task(
            "app.workers.report_validator.validate_community_report",
            args=[str(report_id)],
            queue="default",
        )
        logger.debug(f"Enqueued validation task for report {report_id}")

        return PriceReportResponse(
            id=report_id,
            product_id=product.id,
            product_name=product.name,
            store_id=store.id,
            store_name=store.name,
            price_aud=request.price_cents / 100,
            was_price_aud=request.was_price_cents / 100 if request.was_price_cents else None,
            confidence=0.5,
            is_verified=False,
            reporter_id=user.id,
            created_at=price_report.created_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to create price report: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create price report",
        )


@router.get(
    "/moderation/reports",
    response_model=FlaggedReportsListResponse,
    dependencies=[Depends(get_current_user)],
)
async def get_flagged_reports(
    status_filter: Annotated[str | None, Query()] = "flagged",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
) -> FlaggedReportsListResponse:
    """
    Retrieve flagged price reports for moderation review.

    Admin-only endpoint. Returns reports marked for manual review by validators.

    Query Parameters:
        status_filter: Filter by status ('flagged', 'all'; default 'flagged')
        limit: Maximum results (1-500, default 50)
        offset: Pagination offset (default 0)

    Returns:
        FlaggedReportsListResponse: List of flagged reports with reporter details

    Raises:
        HTTPException: 401 if not authenticated
    """
    logger.info(f"Retrieving flagged reports (limit={limit}, offset={offset})")

    try:
        # Build query
        query = select(PriceReport).where(PriceReport.is_flagged == True)

        # Count total
        total_count = db.execute(
            select(func.count()).select_from(PriceReport).where(PriceReport.is_flagged == True)
        ).scalar()

        # Get paginated results
        reports = db.execute(
            query.order_by(desc(PriceReport.created_at)).limit(limit).offset(offset)
        ).scalars().all()

        # Build responses with reporter details
        report_responses = []
        for report in reports:
            reporter = db.execute(
                select(UserProfile).where(UserProfile.id == report.reporter_user_id)
            ).scalar_one_or_none()

            product = db.execute(
                select(Product).where(Product.id == report.product_id)
            ).scalar_one_or_none()

            store = db.execute(
                select(Store).where(Store.id == report.store_id)
            ).scalar_one_or_none()

            # Calculate stats: 30-day mean and stdev
            historical = db.execute(
                select(PriceReport).where(
                    and_(
                        PriceReport.product_id == report.product_id,
                        PriceReport.store_id == report.store_id,
                        PriceReport.created_at >= datetime.now(timezone.utc).__sub__(
                            __import__("datetime").timedelta(days=30)
                        ),
                    )
                )
            ).scalars().all()

            mean_price = (
                sum(p.price_cents for p in historical) / len(historical)
                if historical
                else report.price_cents
            )

            report_responses.append(
                FlaggedReportResponse(
                    id=report.id,
                    product_id=product.id if product else None,
                    product_name=product.name if product else "Unknown",
                    store_id=store.id if store else None,
                    store_name=store.name if store else "Unknown",
                    price_aud=report.price_cents / 100,
                    was_price_aud=report.was_price_cents / 100 if report.was_price_cents else None,
                    confidence=report.confidence,
                    is_verified=report.is_verified,
                    reporter_email=reporter.email if reporter else "Unknown",
                    reporter_reputation=reporter.reputation_score if reporter else 0.0,
                    reporter_contribution_count=reporter.contribution_count if reporter else 0,
                    mean_price_30d_aud=mean_price / 100,
                    is_flagged=report.is_flagged,
                    created_at=report.created_at,
                )
            )

        return FlaggedReportsListResponse(
            total_count=total_count,
            count=len(report_responses),
            limit=limit,
            offset=offset,
            reports=report_responses,
        )

    except Exception as e:
        logger.exception(f"Failed to retrieve flagged reports: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve flagged reports",
        )


@router.post(
    "/{report_id}/upvote",
    response_model=dict,
    dependencies=[Depends(get_current_user)],
)
async def upvote_report(
    report_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Upvote a price report as helpful.

    Increments report upvote count and increases reporter's reputation by +0.01.

    Path Parameters:
        report_id: UUID of price report to upvote

    Returns:
        dict: Success status and new report upvote count

    Raises:
        HTTPException: 404 if report not found
    """
    logger.info(f"Upvoting report {report_id} by user {current_user.firebase_uid}")

    try:
        # Get report
        report = db.execute(
            select(PriceReport).where(PriceReport.id == report_id)
        ).scalar_one_or_none()

        if not report:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Report {report_id} not found",
            )

        # Increment upvote count (initialize if null)
        if not hasattr(report, "upvote_count") or report.upvote_count is None:
            report.upvote_count = 1
        else:
            report.upvote_count += 1

        # Increase reporter's reputation
        if report.reporter:
            report.reporter.reputation_score = min(
                5.0, report.reporter.reputation_score + 0.01
            )
            logger.debug(
                f"Increased reputation for {report.reporter.email}: {report.reporter.reputation_score:.2f}"
            )

        db.commit()

        logger.info(f"Report {report_id} upvoted successfully")

        return {
            "status": "success",
            "upvote_count": report.upvote_count,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to upvote report {report_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upvote report",
        )


@router.post(
    "/{report_id}/dispute",
    response_model=dict,
    dependencies=[Depends(get_current_user)],
)
async def dispute_report(
    report_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Dispute a price report as inaccurate.

    Flags report for review and adds to moderation queue.

    Path Parameters:
        report_id: UUID of price report to dispute

    Returns:
        dict: Success status

    Raises:
        HTTPException: 404 if report not found
    """
    logger.info(f"Disputing report {report_id} by user {current_user.firebase_uid}")

    try:
        # Get report
        report = db.execute(
            select(PriceReport).where(PriceReport.id == report_id)
        ).scalar_one_or_none()

        if not report:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Report {report_id} not found",
            )

        # Flag for review
        report.is_flagged = True
        db.commit()

        logger.info(f"Report {report_id} flagged for review due to dispute")

        return {
            "status": "success",
            "message": "Report flagged for moderation review",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to dispute report {report_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to dispute report",
        )
