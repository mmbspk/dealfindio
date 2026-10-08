"""
Email notification sender via Resend (https://resend.com).

Sends transactional emails: price alert notifications and weekly deal digests.
Disabled when RESEND_API_KEY is empty — all calls return False silently.

Unsubscribe links are signed with itsdangerous to allow one-click opt-out
without requiring login.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from html import escape as _html_escape
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
_DIGEST_SIZE_BUDGET = 80_000    # Rebuild digest without images if body HTML > 80 KB
_EMAIL_CLIP_WARN_BYTES = 90_000  # Log warning if full email HTML > 90 KB


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
    """Send email via Resend API. Returns message ID or None on failure.

    Logs a WARNING when HTML exceeds _EMAIL_CLIP_WARN_BYTES (Gmail's 102 KB clipping
    threshold) but still sends — this is observability only.
    """
    if not _is_email_enabled():
        logger.debug("Email skipped: RESEND_API_KEY not configured")
        return None
    size = len(html.encode())
    if size > _EMAIL_CLIP_WARN_BYTES:
        logger.warning(f"Email to {to} is {size} bytes; may be clipped by Gmail (threshold {_EMAIL_CLIP_WARN_BYTES})")
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


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _build_subject(
    trigger_type: str,
    product_name: str,
    price: float,
    pct_off: int | None = None,
) -> str:
    """Build email subject line guaranteed <= 50 chars for mobile preview."""
    if trigger_type == "price_match":
        price_str = f"${price:.2f}"
        # "You hit your target — " (22 chars) + " at " (4 chars) = 26 fixed chars
        budget = max(50 - 26 - len(price_str), 5)
        return f"You hit your target — {_truncate(product_name, budget)} at {price_str}"
    if trigger_type == "pct_off_match":
        pct = pct_off or 0
        return f"{pct}% off: {_truncate(product_name, 38)}"
    if trigger_type == "flash_sale":
        pct = pct_off or 0
        return f"Flash deal: {_truncate(product_name, 25)} {pct}% off"
    if trigger_type == "prediction_drop":
        return f"Good time to buy: {_truncate(product_name, 30)}"
    return f"Deal alert: {_truncate(product_name, 32)}"


def _product_card(
    product_url: str,
    product_name: str,
    price: float,
    was_price: float | None,
    store_name: str,
    image_url: str | None,
) -> str:
    """Mobile-first product card: CDN-linked thumbnail (never base64) + price badge + store."""
    safe_name = _html_escape(product_name)
    safe_store = _html_escape(store_name)
    img_cell = ""
    if image_url and image_url.startswith("https://"):
        safe_img = _html_escape(image_url, quote=True)
        img_cell = (
            f'<td width="80" style="vertical-align:top;padding-right:16px;">'
            f'<img src="{safe_img}" alt="" width="80" height="80"'
            f' style="width:80px;height:80px;object-fit:contain;border-radius:8px;'
            f'border:1px solid #e5e7eb;display:block;" /></td>'
        )
    return (
        f'<a href="{product_url}" style="display:block;text-decoration:none;color:inherit;margin:0 0 20px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" role="presentation"><tr>'
        f"{img_cell}"
        f'<td style="vertical-align:top;">'
        f'<div style="font-size:15px;font-weight:700;color:#111827;margin:0 0 6px;line-height:1.4;">{safe_name}</div>'
        f'<div style="margin:0 0 8px;">{_price_badge(price, was_price)}</div>'
        f'<p style="margin:0;font-size:13px;color:#6b7280;">at <strong style="color:#374151;">{safe_store}</strong></p>'
        f"</td></tr></table></a>"
    )


def _build_digest_content(
    grouped: dict[str, list[dict[str, Any]]],
    base_url: str,
    *,
    include_images: bool,
) -> str:
    """Render weekly digest body HTML grouped by list, optionally with product thumbnails."""
    parts: list[str] = []
    for _list_key, deals in grouped.items():
        if not deals:
            continue
        first = deals[0]
        list_name = first.get("list_name")
        list_emoji = first.get("list_emoji") or ""
        if list_name:
            header_text = _html_escape(f"{list_emoji} {list_name}".strip())
            parts.append(
                f'<h3 style="font-size:16px;font-weight:700;color:#374151;margin:24px 0 12px;'
                f'padding-bottom:8px;border-bottom:2px solid #4f46e5;">{header_text}</h3>'
            )
        for d in deals:
            product_url = f"{base_url}/product/{d['product_id']}"
            image_url = d.get("image_url") if include_images else None
            parts.append(
                _product_card(
                    product_url,
                    d["product_name"],
                    d["price"],
                    d.get("was_price"),
                    d["store_name"],
                    image_url,
                )
            )
    return "\n".join(parts)


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
    image_url: str | None = None,
    pct_off: int | None = None,
) -> str | None:
    """Send a price alert email. Returns Resend message ID or None."""
    if not _is_email_enabled():
        return None

    trigger_labels = {
        "price_match": "Target Price Hit",
        "pct_off_match": "Discount Alert",
        "flash_sale": "Flash Deal",
        "prediction_drop": "Good Time to Buy",
    }
    label = trigger_labels.get(trigger_type, "Price Alert")
    product_url = f"{_base_url()}/product/{product_id}"
    unsub_token = generate_unsubscribe_token(user_id, "email_price_match")
    unsub_url = f"{_base_url()}/api/v1/notifications/unsubscribe?token={unsub_token}"

    subject = _build_subject(trigger_type, product_name, price, pct_off=pct_off)
    card = _product_card(product_url, product_name, price, was_price, store_name, image_url)

    content = f"""
    <p style="margin:0 0 8px;font-size:13px;font-weight:600;color:#4f46e5;text-transform:uppercase;letter-spacing:0.5px;">{label}</p>
    <h2 style="margin:0 0 20px;font-size:20px;font-weight:700;color:#111827;line-height:1.3;">{product_name}</h2>
    {card}
    <p style="margin:24px 0 0;font-size:13px;color:#9ca3af;">
      You're receiving this because you're watching this product on DealFindio.
    </p>
    """
    html = _html_wrapper(content, unsub_url)
    return _send_email(to, subject, html, unsubscribe_url=unsub_url)


def send_weekly_digest_email(
    to: str,
    user_id: str,
    deals: list[dict[str, Any]],
) -> str | None:
    """
    Send weekly deal digest. `deals` is a list of dicts with keys:
    product_name, price, was_price, store_name, product_id, pct_off (optional),
    list_id (optional), list_name (optional), list_emoji (optional), image_url (optional).
    Returns Resend message ID or None.
    """
    if not _is_email_enabled() or not deals:
        return None

    unsub_token = generate_unsubscribe_token(user_id, "email_weekly_digest")
    unsub_url = f"{_base_url()}/api/v1/notifications/unsubscribe?token={unsub_token}"

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in deals:
        key = d.get("list_id") or "__other__"
        grouped[key].append(d)

    intro = (
        '<h2 style="margin:0 0 20px;font-size:20px;font-weight:700;color:#111827;">'
        "Your deals this week \U0001f6d2</h2>"
    )
    cta = (
        f'<br><a href="{_base_url()}/watchlist"'
        f' style="display:inline-block;background:#4f46e5;color:#fff;font-weight:600;'
        f'font-size:15px;padding:12px 28px;border-radius:8px;text-decoration:none;">'
        f"View All Deals &rarr;</a>"
    )

    body_html = _build_digest_content(grouped, _base_url(), include_images=True)
    if len((intro + body_html + cta).encode()) > _DIGEST_SIZE_BUDGET:
        logger.warning(
            f"Digest body {len(body_html.encode())} bytes exceeds {_DIGEST_SIZE_BUDGET}; "
            f"rebuilding without images for to={to}"
        )
        body_html = _build_digest_content(grouped, _base_url(), include_images=False)

    content = intro + body_html + cta
    html = _html_wrapper(content, unsub_url)
    return _send_email(to, "Your deals this week \U0001f6d2", html, unsubscribe_url=unsub_url)
