"""
Push + email notification worker tasks.

Handles matching new prices against user watchlists and dispatching
notifications via Expo push (mobile) and Resend email.

Bugs fixed from original:
  - reporter_user_id NULL filter (was always false in SQL for scraper sources)
  - FCM-only filter removed (email users have no FCM token but still need alerts)
  - catalog_ingest path now triggers this via catalog_ingest.py
  - preferred_chains now checked in check_watchlist_for_price
  - flash_sale category (>=40% off) added
  - Notification preferences respected per user
  - Quiet hours check
  - Uses Expo push instead of firebase_admin.messaging
  - Email dispatch via Resend
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import TypedDict
from uuid import UUID

from sqlalchemy import or_

from app.config import get_settings
from app.deps import _SessionLocal
from app.models import Notification, PriceReport, UserProfile, UserWatchlist
from app.workers.celery_app import celery_app
from app.workers.tasks_base import CallbackTask

logger = logging.getLogger(__name__)
settings = get_settings()

_FLASH_SALE_THRESHOLD = 40.0  # % off — above this becomes "flash_sale"


class NotificationResult(TypedDict):
    watchlists_matched: int
    notifications_sent: int
    status: str


def get_db_session():
    if not _SessionLocal:
        raise RuntimeError("Database not initialized")
    return _SessionLocal()


def _in_quiet_hours(user: UserProfile) -> bool:
    """Return True if current time in user's timezone falls in their quiet window."""
    if user.notif_quiet_hours_start is None or user.notif_quiet_hours_end is None:
        return False
    try:
        import zoneinfo

        tz = zoneinfo.ZoneInfo(user.notif_timezone or "Australia/Sydney")
        local_hour = datetime.now(tz).hour
        start = user.notif_quiet_hours_start
        end = user.notif_quiet_hours_end
        if start <= end:
            return start <= local_hour < end
        else:  # wraps midnight: e.g. 22 → 7
            return local_hour >= start or local_hour < end
    except Exception:
        return False


def _send_push(user: UserProfile, title: str, body: str, data: dict) -> str | None:
    """Send Expo push. Returns receipt_id or None. Synchronous (Celery task context)."""
    if not user.fcm_token:
        return None
    from app.services.push_sender import send_expo_push_sync

    success, receipt_id = send_expo_push_sync(user.fcm_token, title, body, data)
    if not success:
        # DeviceNotRegistered — clear stale token
        return None
    return receipt_id


@celery_app.task(
    bind=True,
    name="app.workers.notification_tasks.check_watchlist_for_price",
    queue="notification",
    base=CallbackTask,
    time_limit=60,
    soft_time_limit=50,
    max_retries=3,
    default_retry_delay=30,
)
def check_watchlist_for_price(self, price_report_id: str) -> NotificationResult:
    """
    Check if a newly created PriceReport matches any user watchlists and
    dispatch push + email notifications accordingly.

    Triggered by:
      - ingest_scraped_price() in product_dedup.py (flyer scraper path)
      - validate_community_report() in report_validator.py
      - ingest_catalog_product() in catalog_ingest.py
    """
    logger.info(f"check_watchlist_for_price: {price_report_id}", extra={"task_id": self.request.id})
    session = None
    notifications_sent = 0
    watchlists_matched = 0

    try:
        try:
            price_report_uuid = UUID(price_report_id)
        except ValueError:
            logger.error(f"Invalid UUID: {price_report_id}")
            return {"watchlists_matched": 0, "notifications_sent": 0, "status": "error"}

        session = get_db_session()
        price_report = session.query(PriceReport).filter_by(id=price_report_uuid).first()
        if not price_report:
            logger.warning(f"PriceReport not found: {price_report_id}")
            return {"watchlists_matched": 0, "notifications_sent": 0, "status": "error"}

        # Fetch ALL active watchlist items for this product, regardless of channel.
        # Exclude self-reporting (but safely handle NULL reporter_user_id for scrapers).
        watchlist_items = (
            session.query(UserWatchlist)
            .join(UserProfile, UserWatchlist.user_id == UserProfile.id)
            .filter(
                UserWatchlist.product_id == price_report.product_id,
                UserWatchlist.is_active == True,
                # Fix: reporter_user_id IS NULL for scraper sources → don't exclude anyone
                or_(
                    price_report.reporter_user_id == None,
                    UserWatchlist.user_id != price_report.reporter_user_id,
                ),
            )
            .all()
        )

        logger.info(f"Found {len(watchlist_items)} potential watchlist matches")
        watchlists_matched = len(watchlist_items)
        now = datetime.now(timezone.utc)

        for watchlist_item in watchlist_items:
            try:
                # Check preferred_chains filter
                if watchlist_item.preferred_chains and price_report.store:
                    store_chain = getattr(price_report.store, "chain", None)
                    if store_chain and store_chain not in watchlist_item.preferred_chains:
                        continue

                # Determine trigger type
                price_match = (
                    watchlist_item.target_price_cents
                    and price_report.price_cents <= watchlist_item.target_price_cents
                )
                pct_off_match = (
                    watchlist_item.target_pct_off
                    and price_report.sale_pct_off
                    and price_report.sale_pct_off >= watchlist_item.target_pct_off
                )

                if not (price_match or pct_off_match):
                    continue

                # Flash sale overrides pct_off_match when >=40% off
                if price_report.sale_pct_off and price_report.sale_pct_off >= _FLASH_SALE_THRESHOLD:
                    trigger_type = "flash_sale"
                elif price_match:
                    trigger_type = "price_match"
                else:
                    trigger_type = "pct_off_match"

                # 24-hour dedup
                recent = (
                    session.query(Notification)
                    .filter(
                        Notification.user_id == watchlist_item.user_id,
                        Notification.watchlist_id == watchlist_item.id,
                        Notification.trigger_type == trigger_type,
                        Notification.sent_at >= now - timedelta(hours=24),
                    )
                    .first()
                )
                if recent:
                    logger.debug(f"Dedup skip: watchlist={watchlist_item.id} type={trigger_type}")
                    continue

                user = session.query(UserProfile).filter_by(id=watchlist_item.user_id).first()
                if not user:
                    continue

                # Resolve per-user channel preferences
                pref_map = {
                    "price_match": ("notif_push_price_match", "notif_email_price_match"),
                    "pct_off_match": ("notif_push_pct_off", "notif_email_pct_off"),
                    "flash_sale": ("notif_push_flash_sale", "notif_email_pct_off"),
                    "prediction_drop": ("notif_push_prediction", "notif_email_prediction"),
                }
                push_pref_attr, email_pref_attr = pref_map.get(
                    trigger_type, ("notif_push_price_match", "notif_email_price_match")
                )
                send_push_notif = getattr(user, push_pref_attr, True)
                send_email_notif = getattr(user, email_pref_attr, False)

                quiet = _in_quiet_hours(user)
                if quiet:
                    send_push_notif = False  # suppress push during quiet hours; email still goes

                # Build message content
                product_name = price_report.product.name if price_report.product else "Product"
                price_aud = price_report.price_cents / 100
                store_name = price_report.store.name if price_report.store else "a store"
                was_price_aud = (
                    price_report.was_price_cents / 100 if price_report.was_price_cents else None
                )

                if trigger_type == "flash_sale":
                    title = f"Flash Sale: {product_name}"
                    pct = round(price_report.sale_pct_off)
                    body = f"{pct}% off — ${price_aud:.2f} at {store_name}"
                elif trigger_type == "price_match":
                    title = f"Price Drop: {product_name}"
                    body = f"Now ${price_aud:.2f} at {store_name}"
                else:
                    pct = round(price_report.sale_pct_off or 0)
                    title = f"Deal Found: {product_name}"
                    body = f"{pct}% off — ${price_aud:.2f} at {store_name}"

                if was_price_aud:
                    body += f" (was ${was_price_aud:.2f})"

                push_receipt = None
                email_msg_id = None

                # Send push
                if send_push_notif and user.fcm_token:
                    push_receipt = _send_push(
                        user,
                        title,
                        body,
                        {
                            "product_id": str(price_report.product_id),
                            "store_id": str(price_report.store_id),
                            "price_cents": str(price_report.price_cents),
                            "trigger_type": trigger_type,
                        },
                    )
                    _log_notification(
                        session,
                        user,
                        watchlist_item,
                        price_report_uuid,
                        trigger_type,
                        title,
                        body,
                        "push",
                        fcm_message_id=push_receipt,
                        is_delivered=push_receipt is not None,
                    )
                    if push_receipt:
                        notifications_sent += 1

                # Always log in-app notification (even if push/email off)
                _log_notification(
                    session,
                    user,
                    watchlist_item,
                    price_report_uuid,
                    trigger_type,
                    title,
                    body,
                    "in_app",
                    is_delivered=True,
                )

                # Send email
                if send_email_notif and user.email:
                    from app.services.email_sender import send_price_alert_email

                    email_msg_id = send_price_alert_email(
                        to=user.email,
                        user_id=str(user.id),
                        product_name=product_name,
                        price=price_aud,
                        was_price=was_price_aud,
                        store_name=store_name,
                        product_id=str(price_report.product_id),
                        trigger_type=trigger_type,
                    )
                    if email_msg_id:
                        _log_notification(
                            session,
                            user,
                            watchlist_item,
                            price_report_uuid,
                            trigger_type,
                            title,
                            body,
                            "email",
                            email_message_id=email_msg_id,
                            is_delivered=True,
                        )
                        notifications_sent += 1

            except Exception as e:
                logger.error(f"Error processing watchlist {watchlist_item.id}: {e}", exc_info=True)
                continue

        logger.info(f"check_watchlist_for_price done: {notifications_sent} sent")
        return {
            "watchlists_matched": watchlists_matched,
            "notifications_sent": notifications_sent,
            "status": "success" if notifications_sent > 0 else "no_matches",
        }

    except Exception as e:
        logger.exception(f"check_watchlist_for_price failed: {e}")
        raise self.retry(exc=e, countdown=30, max_retries=3)
    finally:
        if session:
            session.close()


def _log_notification(
    session,
    user: UserProfile,
    watchlist_item: UserWatchlist,
    price_report_id: UUID,
    trigger_type: str,
    title: str,
    body: str,
    channel: str,
    *,
    fcm_message_id: str | None = None,
    email_message_id: str | None = None,
    is_delivered: bool = False,
) -> None:
    try:
        notif = Notification(
            user_id=watchlist_item.user_id,
            watchlist_id=watchlist_item.id,
            price_report_id=price_report_id,
            trigger_type=trigger_type,
            message_title=title,
            message_body=body,
            channel=channel,
            fcm_message_id=fcm_message_id,
            email_message_id=email_message_id,
            is_delivered=is_delivered,
            sent_at=datetime.now(timezone.utc),
        )
        session.add(notif)
        session.commit()
    except Exception as e:
        logger.error(f"Failed to log {channel} notification: {e}")
        try:
            session.rollback()
        except Exception:
            pass


@celery_app.task(
    bind=True,
    name="app.workers.notification_tasks.notify_prediction_drops",
    queue="notification",
    base=CallbackTask,
    time_limit=300,
    soft_time_limit=270,
    max_retries=2,
    default_retry_delay=60,
)
def notify_prediction_drops(self) -> NotificationResult:
    """
    Send notifications for upcoming ML-predicted price drops.
    Called after refit_and_predict_all when predictions_generated > 0.
    """
    logger.info("notify_prediction_drops started", extra={"task_id": self.request.id})
    session = None
    notifications_sent = 0
    watchlists_matched = 0

    try:
        from app.models import PricePrediction

        session = get_db_session()
        now = datetime.now(timezone.utc)

        watchlist_items = (
            session.query(UserWatchlist)
            .join(UserProfile, UserWatchlist.user_id == UserProfile.id)
            .filter(
                UserWatchlist.is_active == True,
                UserWatchlist.alert_on_prediction == True,
            )
            .all()
        )

        for watchlist_item in watchlist_items:
            try:
                pred_query = session.query(PricePrediction).filter(
                    PricePrediction.product_id == watchlist_item.product_id,
                    PricePrediction.is_likely_sale == True,
                    PricePrediction.prediction_week >= now,
                )
                if watchlist_item.preferred_chains:
                    pred_query = pred_query.filter(
                        PricePrediction.chain.in_(watchlist_item.preferred_chains)
                    )
                prediction = pred_query.order_by(PricePrediction.prediction_week).first()
                if not prediction:
                    continue

                watchlists_matched += 1

                recent = (
                    session.query(Notification)
                    .filter(
                        Notification.user_id == watchlist_item.user_id,
                        Notification.watchlist_id == watchlist_item.id,
                        Notification.trigger_type == "prediction_drop",
                        Notification.sent_at >= now - timedelta(days=7),
                    )
                    .first()
                )
                if recent:
                    continue

                user = session.query(UserProfile).filter_by(id=watchlist_item.user_id).first()
                if not user:
                    continue

                product_name = (
                    watchlist_item.product.name
                    if watchlist_item.product
                    else str(watchlist_item.product_id)
                )
                pred_aud = prediction.predicted_price_cents / 100
                week_str = prediction.prediction_week.strftime("%d %b")
                chain_str = f" at {prediction.chain}" if prediction.chain else ""

                title = f"Price drop predicted: {product_name}"
                body = f"Expected ${pred_aud:.2f}{chain_str} week of {week_str}"

                # Always log in-app
                _log_prediction_notification(
                    session,
                    user,
                    watchlist_item,
                    prediction,
                    title,
                    body,
                    "in_app",
                    is_delivered=True,
                )

                # Push
                if (
                    getattr(user, "notif_push_prediction", True)
                    and user.fcm_token
                    and not _in_quiet_hours(user)
                ):
                    receipt = _send_push(
                        user,
                        title,
                        body,
                        {
                            "product_id": str(watchlist_item.product_id),
                            "predicted_price_cents": str(prediction.predicted_price_cents),
                            "prediction_week": prediction.prediction_week.isoformat(),
                            "chain": prediction.chain or "",
                            "trigger_type": "prediction_drop",
                        },
                    )
                    _log_prediction_notification(
                        session,
                        user,
                        watchlist_item,
                        prediction,
                        title,
                        body,
                        "push",
                        fcm_message_id=receipt,
                        is_delivered=receipt is not None,
                    )
                    if receipt:
                        notifications_sent += 1

                # Email
                if getattr(user, "notif_email_prediction", False) and user.email:
                    from app.services.email_sender import send_price_alert_email

                    msg_id = send_price_alert_email(
                        to=user.email,
                        user_id=str(user.id),
                        product_name=product_name,
                        price=pred_aud,
                        was_price=None,
                        store_name=prediction.chain or "upcoming",
                        product_id=str(watchlist_item.product_id),
                        trigger_type="prediction_drop",
                    )
                    if msg_id:
                        _log_prediction_notification(
                            session,
                            user,
                            watchlist_item,
                            prediction,
                            title,
                            body,
                            "email",
                            email_message_id=msg_id,
                            is_delivered=True,
                        )
                        notifications_sent += 1

            except Exception as e:
                logger.error(f"Error processing watchlist {watchlist_item.id}: {e}", exc_info=True)
                continue

        logger.info(
            f"notify_prediction_drops done: {notifications_sent} sent ({watchlists_matched} matched)"
        )
        return {
            "watchlists_matched": watchlists_matched,
            "notifications_sent": notifications_sent,
            "status": "success",
        }

    except Exception as exc:
        logger.exception(f"notify_prediction_drops failed: {exc}")
        raise self.retry(exc=exc, countdown=60, max_retries=2)
    finally:
        if session:
            session.close()


def _log_prediction_notification(
    session,
    user,
    watchlist_item,
    prediction,
    title,
    body,
    channel,
    *,
    fcm_message_id=None,
    email_message_id=None,
    is_delivered=False,
):
    try:
        notif = Notification(
            user_id=watchlist_item.user_id,
            watchlist_id=watchlist_item.id,
            price_report_id=None,
            trigger_type="prediction_drop",
            message_title=title,
            message_body=body,
            channel=channel,
            fcm_message_id=fcm_message_id,
            email_message_id=email_message_id,
            is_delivered=is_delivered,
            sent_at=datetime.now(timezone.utc),
        )
        session.add(notif)
        session.commit()
    except Exception as e:
        logger.error(f"Failed to log prediction notification: {e}")
        try:
            session.rollback()
        except Exception:
            pass


@celery_app.task(
    bind=True,
    name="app.workers.notification_tasks.send_weekly_digest",
    queue="notification",
    base=CallbackTask,
    time_limit=300,
    soft_time_limit=270,
    max_retries=2,
    default_retry_delay=120,
)
def send_weekly_digest(self) -> dict:
    """
    Send weekly deal digest emails to opted-in users.
    Finds top 5 on-sale products from each user's watchlist.
    Scheduled Monday 7am UTC (5pm AEST) by Celery Beat.
    """
    logger.info("send_weekly_digest started", extra={"task_id": self.request.id})
    session = None
    emails_sent = 0

    try:
        session = get_db_session()
        now = datetime.now(timezone.utc)

        # Users who opted in to weekly digest and have email
        users = (
            session.query(UserProfile)
            .filter(
                UserProfile.notif_email_weekly_digest == True,
                UserProfile.email != None,
            )
            .all()
        )

        for user in users:
            try:
                # Find on-sale products from this user's active watchlist items
                watchlist_items = (
                    session.query(UserWatchlist)
                    .filter(
                        UserWatchlist.user_id == user.id,
                        UserWatchlist.is_active == True,
                    )
                    .all()
                )

                if not watchlist_items:
                    continue

                product_ids = [w.product_id for w in watchlist_items]

                # Get best active price report per product (is_on_sale)
                from sqlalchemy import desc

                from app.models import PriceReport, Product, Store

                deals = []
                for pid in product_ids:
                    report = (
                        session.query(PriceReport)
                        .join(Product, PriceReport.product_id == Product.id)
                        .filter(
                            PriceReport.product_id == pid,
                            PriceReport.is_on_sale == True,
                        )
                        .order_by(desc(PriceReport.sale_pct_off))
                        .first()
                    )

                    if report and report.product:
                        store = session.query(Store).filter_by(id=report.store_id).first()
                        deals.append(
                            {
                                "product_name": report.product.name,
                                "price": report.price_cents / 100,
                                "was_price": report.was_price_cents / 100
                                if report.was_price_cents
                                else None,
                                "store_name": store.name if store else "Store",
                                "product_id": str(pid),
                                "pct_off": round(report.sale_pct_off)
                                if report.sale_pct_off
                                else None,
                            }
                        )

                if not deals:
                    continue

                # Sort by pct_off desc, take top 5
                deals.sort(key=lambda d: d.get("pct_off") or 0, reverse=True)
                deals = deals[:5]

                from app.services.email_sender import send_weekly_digest_email

                msg_id = send_weekly_digest_email(
                    to=user.email,
                    user_id=str(user.id),
                    deals=deals,
                )

                if msg_id:
                    notif = Notification(
                        user_id=user.id,
                        trigger_type="weekly_digest",
                        message_title="Your DealFindio Deals This Week",
                        message_body=f"{len(deals)} deals found on your watchlist",
                        channel="email",
                        email_message_id=msg_id,
                        is_delivered=True,
                        sent_at=now,
                    )
                    session.add(notif)
                    session.commit()
                    emails_sent += 1

            except Exception as e:
                logger.error(f"Weekly digest error for user {user.id}: {e}", exc_info=True)
                continue

        logger.info(f"send_weekly_digest done: {emails_sent} emails sent")
        return {"emails_sent": emails_sent, "status": "success"}

    except Exception as exc:
        logger.exception(f"send_weekly_digest failed: {exc}")
        raise self.retry(exc=exc, countdown=120, max_retries=2)
    finally:
        if session:
            session.close()
