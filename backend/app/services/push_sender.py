"""
Expo Push Notification sender.

Uses Expo's push notification service (https://exp.host/--/api/v2/push/send).
No Firebase service account needed — works with Expo push tokens (ExponentPushToken[...]).

Tokens registered from mobile via Notifications.getExpoPushTokenAsync().
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_EXPO_RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"


def is_expo_token(token: str) -> bool:
    return token.startswith("ExponentPushToken[") or token.startswith("ExpoPushToken[")


async def send_expo_push(
    token: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    badge: int | None = None,
    sound: str = "default",
) -> tuple[bool, str | None]:
    """
    Send a push notification via Expo's push API.

    Returns (success: bool, receipt_id: str | None).
    On DeviceNotRegistered error returns (False, None) — caller should clear the token.
    """
    if not token:
        return False, None

    payload: dict[str, Any] = {
        "to": token,
        "title": title,
        "body": body,
        "sound": sound,
    }
    if data:
        payload["data"] = data
    if badge is not None:
        payload["badge"] = badge

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _EXPO_PUSH_URL,
                json=payload,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            result = resp.json()

        # Expo returns {"data": {"status": "ok", "id": "..."}} or {"data": {"status": "error", ...}}
        ticket = result.get("data", {})
        if ticket.get("status") == "ok":
            receipt_id = ticket.get("id")
            logger.info(f"Expo push sent: receipt_id={receipt_id} token={token[:20]}...")
            return True, receipt_id
        elif ticket.get("details", {}).get("error") == "DeviceNotRegistered":
            logger.warning(f"Expo push: DeviceNotRegistered for token {token[:20]}...")
            return False, None
        else:
            err = ticket.get("message", "unknown error")
            logger.error(f"Expo push error: {err} token={token[:20]}...")
            return False, None

    except httpx.HTTPStatusError as e:
        logger.error(f"Expo push HTTP error {e.response.status_code}: {e.response.text}")
        return False, None
    except Exception as e:
        logger.error(f"Expo push exception: {e}", exc_info=True)
        return False, None


def send_expo_push_sync(
    token: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """Synchronous wrapper for use in Celery tasks."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(send_expo_push(token, title, body, data))
