"""
Encrypted config store for admin-managed settings.

Keys are stored in the `app_config` table.  Secret values are Fernet-encrypted
using CONFIG_ENCRYPTION_KEY from the environment (a URL-safe base64 32-byte key).
Non-secret values (e.g. feature flags, thresholds) are stored plaintext.

Generate a new key:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.app_config import AppConfig

logger = logging.getLogger(__name__)

# Well-known config keys with metadata.
#
# Base fields: key, label, description, group, is_secret.
# Optional typing fields (absence => a plain "string"):
#   "type":    "string" | "bool" | "float" | "int" | "enum"
#   "options": [...]      (enum only — the allowed values)
#   "min"/"max": numeric bounds (float/int only)
#   "default": "<str>"    (effective default shown in the UI when unset)
KNOWN_KEYS: list[dict] = [
    # ── Notifications ────────────────────────────────────────────────────────
    {
        "key": "RESEND_API_KEY",
        "label": "Resend API Key",
        "description": "Transactional email via resend.com. Get from resend.com/api-keys.",
        "group": "Notifications",
        "is_secret": True,
    },
    {
        "key": "EMAIL_FROM_ADDRESS",
        "label": "From Email Address",
        "description": "Sender address for all outbound emails (e.g. alerts@dealfindio.com.au).",
        "group": "Notifications",
        "is_secret": False,
    },
    {
        "key": "EMAIL_FROM_NAME",
        "label": "From Name",
        "description": "Display name for outbound emails (e.g. DealFindio Alerts).",
        "group": "Notifications",
        "is_secret": False,
    },
    # ── Storage ──────────────────────────────────────────────────────────────
    {
        "key": "R2_ACCOUNT_ID",
        "label": "Cloudflare R2 Account ID",
        "description": "Cloudflare account ID for R2 object storage.",
        "group": "Storage",
        "is_secret": False,
    },
    {
        "key": "R2_ACCESS_KEY_ID",
        "label": "R2 Access Key ID",
        "description": "R2 S3-compatible access key ID.",
        "group": "Storage",
        "is_secret": True,
    },
    {
        "key": "R2_SECRET_ACCESS_KEY",
        "label": "R2 Secret Access Key",
        "description": "R2 S3-compatible secret access key.",
        "group": "Storage",
        "is_secret": True,
    },
    {
        "key": "R2_BUCKET_NAME",
        "label": "R2 Bucket Name",
        "description": "Name of the R2 bucket for product images.",
        "group": "Storage",
        "is_secret": False,
    },
    # ── ML / AI ──────────────────────────────────────────────────────────────
    {
        "key": "ANTHROPIC_API_KEY",
        "label": "Anthropic API Key",
        "description": "Used for receipt OCR (Claude vision). Get from console.anthropic.com.",
        "group": "AI",
        "is_secret": True,
    },
    {
        "key": "OPENAI_API_KEY",
        "label": "OpenAI API Key",
        "description": "Used for product name embeddings. Get from platform.openai.com.",
        "group": "AI",
        "is_secret": True,
    },
    {
        "key": "ANTHROPIC_BASE_URL",
        "label": "Anthropic Base URL (proxy)",
        "description": "Optional base URL for the Anthropic API (e.g. a corporate proxy/gateway). "
        "Empty = default https://api.anthropic.com. Takes effect on backend restart.",
        "group": "AI",
        "is_secret": False,
        "type": "string",
        "default": "",
    },
    # ── AI Dedup + GTIN backfill ─────────────────────────────────────────────
    {
        "key": "AI_DEDUP_ENABLED",
        "label": "AI Dedup Enabled",
        "description": "Enable the Claude cross-store dedup judge (makes paid API calls).",
        "group": "AI Dedup",
        "is_secret": False,
        "type": "bool",
        "default": "false",
    },
    {
        "key": "AI_DEDUP_MODEL",
        "label": "AI Dedup Model",
        "description": "Claude model used by the dedup judge.",
        "group": "AI Dedup",
        "is_secret": False,
        "type": "enum",
        "options": ["claude-sonnet-4-6", "claude-opus-5", "claude-haiku-4-5-20251001"],
        "default": "claude-sonnet-4-6",
    },
    {
        "key": "AI_DEDUP_CONFIDENCE_MIN",
        "label": "AI Dedup Min Confidence",
        "description": "Minimum AI confidence (0.0-1.0) to auto-merge; below this stays pending.",
        "group": "AI Dedup",
        "is_secret": False,
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "default": "0.8",
    },
    {
        "key": "AI_DEDUP_MAX_TOKENS",
        "label": "AI Dedup Max Tokens",
        "description": "Max output tokens for the compact JSON verdict.",
        "group": "AI Dedup",
        "is_secret": False,
        "type": "int",
        "min": 1,
        "default": "512",
    },
    {
        "key": "OFF_GTIN_BACKFILL_ENABLED",
        "label": "OFF GTIN Backfill Enabled",
        "description": "Enable Open Food Facts name+brand → real GTIN recovery backfill.",
        "group": "AI Dedup",
        "is_secret": False,
        "type": "bool",
        "default": "false",
    },
    {
        "key": "OFF_GTIN_BACKFILL_LIMIT_PER_RUN",
        "label": "OFF GTIN Backfill Limit Per Run",
        "description": "Max products to attempt GTIN recovery for per run.",
        "group": "AI Dedup",
        "is_secret": False,
        "type": "int",
        "min": 1,
        "default": "200",
    },
    # ── Deals feed ───────────────────────────────────────────────────────────
    {
        "key": "DEALS_FEED_DAYS",
        "label": "Deals Feed Window (days)",
        "description": "How many days back to include on-sale price reports in the deals feed. "
        "Increase if stores are scraped less frequently than once a week.",
        "group": "Deals",
        "is_secret": False,
        "type": "int",
        "min": 1,
        "max": 90,
        "default": "7",
    },
    # ── Monitoring ───────────────────────────────────────────────────────────
    {
        "key": "SLACK_WEBHOOK_URL",
        "label": "Slack Webhook URL",
        "description": "Incoming webhook for task failure alerts.",
        "group": "Monitoring",
        "is_secret": True,
    },
    {
        "key": "SENTRY_DSN",
        "label": "Sentry DSN",
        "description": "Sentry error tracking DSN.",
        "group": "Monitoring",
        "is_secret": True,
    },
]

_KNOWN_KEY_MAP: dict[str, dict] = {k["key"]: k for k in KNOWN_KEYS}


def _fernet() -> Fernet:
    key = get_settings().CONFIG_ENCRYPTION_KEY
    return Fernet(key.encode() if isinstance(key, str) else key)


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(value_enc: str) -> str:
    try:
        return _fernet().decrypt(value_enc.encode()).decode()
    except InvalidToken:
        raise ValueError("Decryption failed — CONFIG_ENCRYPTION_KEY may have changed")


# ── Public API ────────────────────────────────────────────────────────────────


def get_config(key: str, session: Session) -> Optional[str]:
    """Return the plaintext value for a key, or None if not set."""
    row = session.get(AppConfig, key)
    if row is None or row.value_enc is None:
        return None
    if row.is_secret:
        return _decrypt(row.value_enc)
    return row.value_enc


def set_config(key: str, value: str, session: Session, description: str | None = None) -> None:
    """Upsert a config value, encrypting secrets."""
    meta = _KNOWN_KEY_MAP.get(key, {})
    is_secret = meta.get("is_secret", True)
    stored = _encrypt(value) if is_secret else value
    row = session.get(AppConfig, key)
    if row is None:
        row = AppConfig(
            key=key,
            value_enc=stored,
            is_secret=is_secret,
            description=description or meta.get("description"),
        )
        session.add(row)
    else:
        row.value_enc = stored
        row.is_secret = is_secret
        if description:
            row.description = description
    session.flush()


def delete_config(key: str, session: Session) -> bool:
    """Delete a config entry. Returns True if it existed."""
    row = session.get(AppConfig, key)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def list_configs(session: Session) -> list[dict]:
    """
    Return all known keys with their status and (for non-secrets) their value.
    Secrets are returned masked — never plaintext in the list response.
    """
    stored: dict[str, AppConfig] = {r.key: r for r in session.query(AppConfig).all()}
    result = []
    for meta in KNOWN_KEYS:
        key = meta["key"]
        row = stored.get(key)
        is_set = row is not None and row.value_enc is not None
        entry: dict = {
            "key": key,
            "label": meta["label"],
            "description": meta["description"],
            "group": meta["group"],
            "is_secret": meta["is_secret"],
            "is_set": is_set,
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
            # Typing metadata (absent => a plain string; see KNOWN_KEYS header).
            "type": meta.get("type", "string"),
            "options": meta.get("options"),
            "min_value": meta.get("min"),
            "max_value": meta.get("max"),
            "default": meta.get("default"),
        }
        # Expose plaintext value only for non-secret entries
        if is_set and not meta["is_secret"]:
            entry["value"] = row.value_enc  # stored plaintext
        else:
            entry["value"] = None
        result.append(entry)
    return result


def get_runtime_value(key: str, session: Session) -> Optional[str]:
    """
    Resolve a config value: DB first, falls back to the env/settings value.
    Used by services that want to pick up admin-set keys at runtime.
    """
    db_val = get_config(key, session)
    if db_val:
        return db_val
    # Fall back to settings
    settings = get_settings()
    return getattr(settings, key, None) or None


_TRUTHY = {"true", "1", "yes", "on"}


def _coerce(value: str, type_: str):
    """Coerce a stored string to its typed Python value. Raises on bad input."""
    if type_ == "bool":
        return str(value).strip().lower() in _TRUTHY
    if type_ == "float":
        return float(value)
    if type_ == "int":
        return int(value)
    # "string" / "enum" — passthrough
    return value


def get_runtime_typed(key: str, session: Session):
    """
    Resolve a config value and coerce it to its declared type.

    DB override first (coerced by the key's ``type``), else the already-typed
    ``Settings`` attribute. A malformed stored string (e.g. a non-numeric int)
    falls back to the settings default rather than crashing a run — the store is
    validated on write, but this keeps a live run resilient to bad data.
    """
    meta = _KNOWN_KEY_MAP.get(key, {})
    type_ = meta.get("type", "string")
    db_val = get_config(key, session)
    if db_val is not None:
        try:
            return _coerce(db_val, type_)
        except (ValueError, TypeError):
            logger.warning(
                "Config %s has an invalid stored value %r; using settings default", key, db_val
            )
    settings = get_settings()
    return getattr(settings, key, None)


def validate_value(key: str, value: str) -> str:
    """
    Validate a proposed value for a known key and return the normalized string
    to store. Raises ``ValueError`` on invalid input (the endpoint maps to 422).

    Unknown keys and plain strings pass through unchanged.
    """
    meta = _KNOWN_KEY_MAP.get(key, {})
    type_ = meta.get("type", "string")

    if type_ == "bool":
        if str(value).strip().lower() in _TRUTHY:
            return "true"
        if str(value).strip().lower() in {"false", "0", "no", "off"}:
            return "false"
        raise ValueError(f"{key} must be a boolean (true/false)")

    if type_ == "enum":
        options = meta.get("options", [])
        if value not in options:
            raise ValueError(f"{key} must be one of: {', '.join(options)}")
        return value

    if type_ in ("float", "int"):
        try:
            num = float(value) if type_ == "float" else int(value)
        except (ValueError, TypeError):
            raise ValueError(f"{key} must be a{'n integer' if type_ == 'int' else ' number'}")
        lo, hi = meta.get("min"), meta.get("max")
        if lo is not None and num < lo:
            raise ValueError(f"{key} must be >= {lo}")
        if hi is not None and num > hi:
            raise ValueError(f"{key} must be <= {hi}")
        return str(num)

    return value
