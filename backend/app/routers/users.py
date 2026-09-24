"""
User profile and reputation system router.

Implements REST API for user profiles including:
- GET /api/v1/users/me: Get current authenticated user profile
- GET /api/v1/users/{user_id}/profile: Get public user profile with reputation
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models import PriceReport, User, UserProfile
from app.schemas.user import (
    UserProfilePublicResponse,
    UserProfileResponse,
    UserProfileUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Users"])


def calculate_tier(contribution_count: int) -> str:
    """
    Calculate user tier based on contribution count.

    Tiers:
        bronze: 0-10 contributions
        silver: 11-50 contributions
        gold: 50+ contributions

    Args:
        contribution_count: Number of price reports contributed

    Returns:
        str: Tier name (bronze, silver, gold)
    """
    if contribution_count >= 50:
        return "gold"
    elif contribution_count >= 11:
        return "silver"
    else:
        return "bronze"


@router.get("/me", response_model=UserProfileResponse, dependencies=[Depends(get_current_user)])
async def get_current_user_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserProfileResponse:
    """
    Get current authenticated user's profile.

    Returns full profile including reputation score and tier.

    Returns:
        UserProfileResponse: User profile detail with tier

    Raises:
        HTTPException: 401 if not authenticated, 404 if profile not found
    """
    logger.info(f"Retrieving profile for user: {current_user.firebase_uid}")

    try:
        user_profile = db.execute(
            select(UserProfile).where(UserProfile.firebase_uid == current_user.firebase_uid)
        ).scalar_one_or_none()

        if not user_profile:
            logger.warning(f"User profile not found: {current_user.firebase_uid}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found",
            )

        tier = calculate_tier(user_profile.contribution_count)

        return UserProfileResponse(
            id=user_profile.id,
            firebase_uid=user_profile.firebase_uid,
            email=user_profile.email,
            display_name=user_profile.display_name,
            reputation_score=user_profile.reputation_score,
            tier=tier,
            contribution_count=user_profile.contribution_count,
            home_lat=user_profile.home_lat,
            home_lon=user_profile.home_lon,
            default_radius_km=user_profile.default_radius_km,
            is_admin=user_profile.is_admin,
            created_at=user_profile.created_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to retrieve user profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user profile",
        )


@router.patch("/me", response_model=UserProfileResponse)
async def update_current_user_profile(
    body: UserProfileUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserProfileResponse:
    """Update the authenticated user's editable profile fields."""
    user_profile = db.execute(
        select(UserProfile).where(UserProfile.firebase_uid == current_user.firebase_uid)
    ).scalar_one_or_none()

    if not user_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found")

    if body.display_name is not None:
        user_profile.display_name = body.display_name
    if body.home_lat is not None:
        user_profile.home_lat = body.home_lat
    if body.home_lon is not None:
        user_profile.home_lon = body.home_lon
    if body.default_radius_km is not None:
        user_profile.default_radius_km = body.default_radius_km

    db.commit()
    db.refresh(user_profile)

    tier = calculate_tier(user_profile.contribution_count)
    return UserProfileResponse(
        id=user_profile.id,
        firebase_uid=user_profile.firebase_uid,
        email=user_profile.email,
        display_name=user_profile.display_name,
        reputation_score=user_profile.reputation_score,
        tier=tier,
        contribution_count=user_profile.contribution_count,
        home_lat=user_profile.home_lat,
        home_lon=user_profile.home_lon,
        default_radius_km=user_profile.default_radius_km,
        is_admin=user_profile.is_admin,
        created_at=user_profile.created_at,
    )


@router.get(
    "/{user_id}/profile",
    response_model=UserProfilePublicResponse,
)
async def get_user_profile(
    user_id: UUID,
    db: Session = Depends(get_db),
) -> UserProfilePublicResponse:
    """
    Get public user profile with reputation metrics.

    Returns public profile information including top reports.

    Path Parameters:
        user_id: UUID of user profile to retrieve

    Returns:
        UserProfilePublicResponse: Public profile with reputation and top reports

    Raises:
        HTTPException: 404 if user not found
    """
    logger.info(f"Retrieving public profile for user: {user_id}")

    try:
        user_profile = db.execute(
            select(UserProfile).where(UserProfile.id == user_id)
        ).scalar_one_or_none()

        if not user_profile:
            logger.warning(f"User profile not found: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User {user_id} not found",
            )

        # Get top 5 recent reports by this user
        top_reports = (
            db.execute(
                select(PriceReport)
                .where(PriceReport.reporter_user_id == user_id)
                .order_by(PriceReport.created_at.desc())
                .limit(5)
            )
            .scalars()
            .all()
        )

        tier = calculate_tier(user_profile.contribution_count)

        top_reports_data = [
            {
                "id": str(report.id),
                "price_aud": report.price_cents / 100,
                "confidence": report.confidence,
                "is_verified": report.is_verified,
                "created_at": report.created_at.isoformat(),
            }
            for report in top_reports
        ]

        return UserProfilePublicResponse(
            display_name=user_profile.display_name,
            reputation_score=user_profile.reputation_score,
            tier=tier,
            contribution_count=user_profile.contribution_count,
            top_reports=top_reports_data,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to retrieve user profile {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user profile",
        )
