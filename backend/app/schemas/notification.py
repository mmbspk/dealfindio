"""
Notification response schemas for REST API.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class FCMTokenRequest(BaseModel):
    fcm_token: str = Field(..., min_length=1, description="Expo or FCM push token")


class NotificationResponse(BaseModel):
    id: UUID
    price_report_id: UUID | None = None
    trigger_type: str
    message_title: str
    message_body: str
    channel: str = "in_app"
    sent_at: datetime
    opened_at: datetime | None = None
    product_name: str | None = None
    store_name: str | None = None

    class Config:
        from_attributes = True


class UnreadCountResponse(BaseModel):
    count: int


class NotificationPreferences(BaseModel):
    notif_push_price_match: bool = True
    notif_push_pct_off: bool = True
    notif_push_prediction: bool = True
    notif_push_flash_sale: bool = True
    notif_email_price_match: bool = False
    notif_email_pct_off: bool = False
    notif_email_prediction: bool = False
    notif_email_weekly_digest: bool = True
    notif_quiet_hours_start: int | None = Field(None, ge=0, le=23)
    notif_quiet_hours_end: int | None = Field(None, ge=0, le=23)
    notif_timezone: str = "Australia/Sydney"

    class Config:
        from_attributes = True
