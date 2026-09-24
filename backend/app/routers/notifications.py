"""
Notifications router.

Endpoints:
  GET  /                         — list notifications (paginated)
  PUT  /{id}/read                — mark one read
  DELETE /{id}                   — delete one
  POST /fcm-token                — register Expo push token
  GET  /unread-count             — unread in-app notification count
  PUT  /read-all                 — mark all in-app read
  GET  /preferences              — get notification preferences
  PUT  /preferences              — update notification preferences
  GET  /unsubscribe?token=…      — one-click email unsubscribe (no auth)
"""

import logging
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, desc, func, select, update
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models import Notification, User, UserProfile
from app.schemas.notification import (
    FCMTokenRequest,
    NotificationPreferences,
    NotificationResponse,
    UnreadCountResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Notifications"])


# ─── helpers ─────────────────────────────────────────────────────────────────


def _require_profile(current_user: User) -> UserProfile:
    if not current_user.profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found")
    return current_user.profile


def _notif_to_response(notif: Notification) -> NotificationResponse:
    product_name = store_name = None
    if notif.price_report:
        product_name = notif.price_report.product.name if notif.price_report.product else None
        store_name = notif.price_report.store.name if notif.price_report.store else None
    return NotificationResponse(
        id=notif.id,
        price_report_id=notif.price_report_id,
        trigger_type=notif.trigger_type,
        message_title=notif.message_title,
        message_body=notif.message_body,
        channel=getattr(notif, "channel", "in_app"),
        sent_at=notif.sent_at,
        opened_at=notif.opened_at,
        product_name=product_name,
        store_name=store_name,
    )


# ─── list / read / delete ─────────────────────────────────────────────────────


@router.get("/", response_model=list[NotificationResponse], summary="List notifications")
async def list_notifications(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[NotificationResponse]:
    profile = _require_profile(current_user)
    notifications = (
        db.execute(
            select(Notification)
            .where(Notification.user_id == profile.id)
            .order_by(desc(Notification.sent_at))
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return [_notif_to_response(n) for n in notifications]


@router.get("/unread-count", response_model=UnreadCountResponse, summary="Unread count")
async def unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UnreadCountResponse:
    profile = _require_profile(current_user)
    count = db.execute(
        select(func.count()).where(
            Notification.user_id == profile.id,
            Notification.opened_at == None,
        )
    ).scalar_one()
    return UnreadCountResponse(count=count)


@router.put("/read-all", summary="Mark all notifications read")
async def read_all(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    profile = _require_profile(current_user)
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(Notification)
        .where(Notification.user_id == profile.id, Notification.opened_at == None)
        .values(opened_at=now)
    )
    db.commit()
    return {"updated": result.rowcount}


@router.put("/{notification_id}/read", response_model=NotificationResponse, summary="Mark read")
async def mark_notification_read(
    notification_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationResponse:
    profile = _require_profile(current_user)
    notif = db.execute(
        select(Notification).where(
            and_(Notification.id == notification_id, Notification.user_id == profile.id)
        )
    ).scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail=f"Notification {notification_id} not found")
    notif.opened_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(notif)
    return _notif_to_response(notif)


@router.delete("/{notification_id}", status_code=204, summary="Delete notification")
async def delete_notification(
    notification_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    profile = _require_profile(current_user)
    notif = db.execute(
        select(Notification).where(
            and_(Notification.id == notification_id, Notification.user_id == profile.id)
        )
    ).scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail=f"Notification {notification_id} not found")
    db.delete(notif)
    db.commit()


# ─── push token ───────────────────────────────────────────────────────────────


@router.post("/fcm-token", status_code=200, summary="Register push token")
async def register_fcm_token(
    request: FCMTokenRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    profile = _require_profile(current_user)
    token = request.fcm_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="fcm_token cannot be empty")

    user_profile = db.execute(
        select(UserProfile).where(UserProfile.id == profile.id)
    ).scalar_one_or_none()
    if not user_profile:
        raise HTTPException(status_code=404, detail="User profile not found")

    user_profile.fcm_token = token
    db.commit()
    logger.info(f"Push token registered for {current_user.email}")

    # Send test push via Expo (graceful on failure)
    from app.services.push_sender import is_expo_token, send_expo_push_sync

    if is_expo_token(token):
        try:
            ok, _ = send_expo_push_sync(
                token,
                "DealFindio Notifications",
                "Push notifications enabled!",
                {"notification_type": "test"},
            )
            if ok:
                return {"status": "success", "message": "Token registered and test push sent"}
        except Exception as e:
            logger.warning(f"Test push failed for {current_user.email}: {e}")

    return {"status": "registered", "message": "Push token registered"}


# ─── preferences ──────────────────────────────────────────────────────────────


@router.get(
    "/preferences", response_model=NotificationPreferences, summary="Get notification preferences"
)
async def get_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationPreferences:
    profile = _require_profile(current_user)
    user_profile = db.execute(
        select(UserProfile).where(UserProfile.id == profile.id)
    ).scalar_one_or_none()
    if not user_profile:
        raise HTTPException(status_code=404, detail="User profile not found")
    return NotificationPreferences.model_validate(user_profile)


@router.put(
    "/preferences",
    response_model=NotificationPreferences,
    summary="Update notification preferences",
)
async def update_preferences(
    prefs: NotificationPreferences,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationPreferences:
    profile = _require_profile(current_user)
    user_profile = db.execute(
        select(UserProfile).where(UserProfile.id == profile.id)
    ).scalar_one_or_none()
    if not user_profile:
        raise HTTPException(status_code=404, detail="User profile not found")

    for field, value in prefs.model_dump().items():
        setattr(user_profile, field, value)
    db.commit()
    db.refresh(user_profile)
    return NotificationPreferences.model_validate(user_profile)


# ─── one-click unsubscribe (no auth) ─────────────────────────────────────────


@router.get("/unsubscribe", response_class=HTMLResponse, include_in_schema=False)
async def unsubscribe(
    token: str = Query(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    from app.services.email_sender import verify_unsubscribe_token

    result = verify_unsubscribe_token(token)
    if not result:
        return HTMLResponse(
            content=_unsub_page("Invalid or expired unsubscribe link.", success=False),
            status_code=400,
        )

    user_id, channel = result
    try:
        from uuid import UUID as _UUID

        user_profile = db.execute(
            select(UserProfile).where(UserProfile.id == _UUID(user_id))
        ).scalar_one_or_none()

        if user_profile:
            if channel == "email_price_match":
                user_profile.notif_email_price_match = False
                user_profile.notif_email_pct_off = False
            elif channel == "email_weekly_digest":
                user_profile.notif_email_weekly_digest = False
            else:
                user_profile.notif_email_price_match = False
                user_profile.notif_email_pct_off = False
                user_profile.notif_email_prediction = False
                user_profile.notif_email_weekly_digest = False
            db.commit()
        return HTMLResponse(
            content=_unsub_page("You've been unsubscribed successfully.", success=True)
        )
    except Exception as e:
        logger.error(f"Unsubscribe failed: {e}")
        db.rollback()
        return HTMLResponse(
            content=_unsub_page("Something went wrong. Please try again.", success=False),
            status_code=500,
        )


def _unsub_page(message: str, *, success: bool) -> str:
    colour = "#059669" if success else "#dc2626"
    icon = "✓" if success else "✗"
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Unsubscribe — DealFindio</title></head>
<body style="margin:0;padding:40px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f3f4f6;text-align:center;">
  <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:12px;padding:40px;box-shadow:0 1px 3px rgba(0,0,0,.1);">
    <div style="font-size:48px;color:{colour};">{icon}</div>
    <h1 style="font-size:22px;font-weight:700;color:#111827;margin:16px 0 8px;">DealFindio</h1>
    <p style="font-size:16px;color:#374151;margin:0 0 24px;">{message}</p>
    <a href="/" style="display:inline-block;background:#4f46e5;color:#fff;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:600;">Go to App</a>
  </div>
</body>
</html>"""
