"""
Email notification sender via Resend (https://resend.com).

Sends transactional emails: price alert notifications and weekly deal digests.
Disabled when RESEND_API_KEY is empty — all calls return False silently.

Unsubscribe links are signed with itsdangerous to allow one-click opt-out
without requiring login.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_RESEND_SEND_URL = "https://api.resend.com/emails"
_UNSUBSCRIBE_SALT = "notif-unsubscribe"
_UNSUBSCRIBE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year
_UNSUBSCRIBE_EMAIL = "unsubscribe@dealfindio.com.au"
_EMAIL_CLIP_WARN_BYTES = 90_000  # Gmail clips at 102 KB; warn before that threshold


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY)


def generate_unsubscribe_token(user_id: str, channel: str) -> str:
    return _signer().dumps({"u": user_id, "c": channel}, salt=_UNSUBSCRIBE_SALT)


def verify_unsubscribe_token(token: str) -> tuple[str, str] | None:
    try:
        data = _signer().loads(token, salt=_UNSUBSCRIBE_SALT, max_age=_UNSUBSCRIBE_MAX_AGE)
        return data["u"], data["c"]
    except (BadSignature, KeyError):
        return None


def _is_email_enabled() -> bool:
    return bool(settings.RESEND_API_KEY)


def _resend_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.RESEND_API_KEY}",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return settings.APP_BASE_URL.rstrip("/")


def _send_email(to: str, subject: str, html: str, unsubscribe_url: str | None = None) -> str | None:
    """Send email via Resend API. Returns message ID or None on failure."""
    if not _is_email_enabled():
        logger.debug("Email skipped: RESEND_API_KEY not configured")
        return None

    size = len(html.encode())
    if size > _EMAIL_CLIP_WARN_BYTES:
        logger.warning(f"Email to {to} is {size} bytes and may be clipped by Gmail (>{_EMAIL_CLIP_WARN_BYTES})")

    payload: dict[str, Any] = {
        "from": f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM_ADDRESS}>",
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if unsubscribe_url:
        payload["headers"] = {
            "List-Unsubscribe": f"<{unsubscribe_url}>, <mailto:{_UNSUBSCRIBE_EMAIL}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }

    try:
        resp = httpx.post(
            _RESEND_SEND_URL,
            headers=_resend_headers(),
            json=payload,
            timeout=10.0,
        )
        resp.raise_for_status()
        msg_id = resp.json().get("id")
        logger.info(f"Email sent via Resend: id={msg_id} to={to}")
        return msg_id
    except httpx.HTTPStatusError as e:
        logger.error(f"Resend HTTP error {e.response.status_code}: {e.response.text}")
        return None
    except Exception as e:
        logger.error(f"Resend send exception: {e}", exc_info=True)
        return None


# ─── HTML template helpers ────────────────────────────────────────────────────


def _html_wrapper(content: str, unsubscribe_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DealFindio Alert</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
        <!-- Header -->
        <tr>
          <td style="background:#4f46e5;border-radius:12px 12px 0 0;padding:24px 32px;">
            <span style="color:#fff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">
              🛒 DealFindio
            </span>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="background:#fff;padding:32px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
            {content}
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background:#f9fafb;border-radius:0 0 12px 12px;padding:20px 32px;border:1px solid #e5e7eb;border-top:none;">
            <p style="margin:0;font-size:12px;color:#9ca3af;text-align:center;">
              DealFindio &middot; Australian Grocery Deal Finder<br>
              <a href="{unsubscribe_url}" style="color:#6b7280;text-decoration:underline;">Unsubscribe from these alerts</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _price_badge(price: float, was_price: float | None) -> str:
    pct = ""
    if was_price and was_price > price:
        pct_off = round((was_price - price) / was_price * 100)
        pct = f'<span style="display:inline-block;background:#fee2e2;color:#dc2626;font-size:12px;font-weight:700;padding:2px 8px;border-radius:20px;margin-left:8px;">-{pct_off}%</span>'
    was = ""
    if was_price:
        was = f'<span style="font-size:16px;color:#9ca3af;text-decoration:line-through;margin-left:8px;">${was_price:.2f}</span>'
    return (
        f'<span style="font-size:28px;font-weight:800;color:#059669;">${price:.2f}</span>{was}{pct}'
    )


# ─── Public API ───────────────────────────────────────────────────────────────


def send_price_alert_email(
    to: str,
    user_id: str,
    product_name: str,
    price: float,
    was_price: float | None,
    store_name: str,
    product_id: str,
    trigger_type: str,
) -> str | None:
    """Send a price alert email. Returns Resend message ID or None."""
    if not _is_email_enabled():
        return None

    trigger_labels = {
        "price_match": "Hit Your Target Price",
        "pct_off_match": "Discount Alert",
        "flash_sale": "Flash Sale",
        "prediction_drop": "Predicted Price Drop",
    }
    label = trigger_labels.get(trigger_type, "Price Alert")
    product_url = f"{_base_url()}/product/{product_id}"
    unsub_token = generate_unsubscribe_token(user_id, "email_price_match")
    unsub_url = f"{_base_url()}/api/v1/notifications/unsubscribe?token={unsub_token}"

    content = f"""
    <p style="margin:0 0 8px;font-size:13px;font-weight:600;color:#4f46e5;text-transform:uppercase;letter-spacing:0.5px;">{label}</p>
    <h2 style="margin:0 0 20px;font-size:20px;font-weight:700;color:#111827;line-height:1.3;">{product_name}</h2>
    <div style="margin:0 0 20px;">{_price_badge(price, was_price)}</div>
    <p style="margin:0 0 24px;font-size:15px;color:#6b7280;">
      Available at <strong style="color:#374151;">{store_name}</strong>
    </p>
    <a href="{product_url}"
       style="display:inline-block;background:#4f46e5;color:#fff;font-weight:600;font-size:15px;
              padding:12px 28px;border-radius:8px;text-decoration:none;">
      View Deal &rarr;
    </a>
    <p style="margin:24px 0 0;font-size:13px;color:#9ca3af;">
      You're receiving this because you're watching this product on DealFindio.
    </p>
    """
    html = _html_wrapper(content, unsub_url)
    subject = f"{label}: {product_name} is now ${price:.2f}"
    return _send_email(to, subject, html, unsubscribe_url=unsub_url)


def send_weekly_digest_email(
    to: str,
    user_id: str,
    deals: list[dict[str, Any]],
) -> str | None:
    """
    Send weekly deal digest. `deals` is a list of dicts with keys:
    product_name, price, was_price, store_name, product_id, pct_off (optional).
    Returns Resend message ID or None.
    """
    if not _is_email_enabled() or not deals:
        return None

    unsub_token = generate_unsubscribe_token(user_id, "email_weekly_digest")
    unsub_url = f"{_base_url()}/api/v1/notifications/unsubscribe?token={unsub_token}"

    rows = ""
    for d in deals[:5]:
        pct = (
            f' &nbsp;<span style="background:#fee2e2;color:#dc2626;font-size:11px;font-weight:700;padding:2px 6px;border-radius:10px;">-{d["pct_off"]}%</span>'
            if d.get("pct_off")
            else ""
        )
        rows += f"""
        <tr>
          <td style="padding:12px 0;border-bottom:1px solid #f3f4f6;">
            <a href="{_base_url()}/product/{d['product_id']}" style="text-decoration:none;">
              <span style="font-size:14px;font-weight:600;color:#111827;">{d['product_name']}</span>{pct}
              <br>
              <span style="font-size:13px;color:#059669;font-weight:700;">${d['price']:.2f}</span>
              &nbsp;<span style="font-size:12px;color:#9ca3af;">{d['store_name']}</span>
            </a>
          </td>
        </tr>"""

    content = f"""
    <p style="margin:0 0 8px;font-size:13px;font-weight:600;color:#4f46e5;text-transform:uppercase;letter-spacing:0.5px;">Weekly Deals Digest</p>
    <h2 style="margin:0 0 20px;font-size:20px;font-weight:700;color:#111827;">Your watchlist deals this week</h2>
    <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
    <br>
    <a href="{_base_url()}/watchlist"
       style="display:inline-block;background:#4f46e5;color:#fff;font-weight:600;font-size:15px;
              padding:12px 28px;border-radius:8px;text-decoration:none;">
      View All Deals &rarr;
    </a>
    """
    html = _html_wrapper(content, unsub_url)
    return _send_email(to, "Your DealFindio Deals This Week", html, unsubscribe_url=unsub_url)
