"""
User profile request and response schemas for REST API.

Defines Pydantic models for user profile details and public profiles.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class UserProfileResponse(BaseModel):
    """Response schema for authenticated user's full profile."""

    id: UUID = Field(..., description="User profile UUID")
    firebase_uid: str = Field(..., description="Firebase authentication UID")
    email: str | None = Field(None, description="User email address")
    display_name: str | None = Field(None, description="User's display name")
    reputation_score: float = Field(
        ...,
        ge=0.0,
        le=5.0,
        description="Reputation score (0.0-5.0)",
    )
    tier: str = Field(
        ...,
        description="User tier (bronze, silver, gold)",
    )
    contribution_count: int = Field(
        ...,
        ge=0,
        description="Number of price reports contributed",
    )
    home_lat: float | None = Field(None, description="Default home latitude")
    home_lon: float | None = Field(None, description="Default home longitude")
    default_radius_km: int = Field(..., description="Default search radius in km")
    is_admin: bool = Field(False, description="Whether user has admin privileges")
    created_at: datetime = Field(..., description="Account creation timestamp")

    class Config:
        from_attributes = True


class UserProfilePublicResponse(BaseModel):
    """Response schema for public user profile."""

    display_name: str | None = Field(None, description="User's display name")
    reputation_score: float = Field(
        ...,
        ge=0.0,
        le=5.0,
        description="Reputation score (0.0-5.0)",
    )
    tier: str = Field(
        ...,
        description="User tier (bronze, silver, gold)",
    )
    contribution_count: int = Field(
        ...,
        ge=0,
        description="Number of price reports contributed",
    )
    top_reports: list[dict] = Field(
        ...,
        description="List of user's top 5 recent reports",
    )


class UserProfileUpdateRequest(BaseModel):
    """Request schema for updating user profile."""

    display_name: str | None = Field(
        None,
        max_length=255,
        description="Update display name",
    )
    home_lat: float | None = Field(
        None,
        ge=-90,
        le=90,
        description="Update home latitude",
    )
    home_lon: float | None = Field(
        None,
        ge=-180,
        le=180,
        description="Update home longitude",
    )
    default_radius_km: int | None = Field(
        None,
        ge=1,
        le=100,
        description="Update default search radius",
    )
