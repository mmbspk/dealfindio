"""
Object storage service — the single write path for binary assets.

Product images (Feature A), receipts, and catalog assets (Feature B) all
persist through this module so there is exactly one place that knows how to
talk to Cloudflare R2 / AWS S3 / the local filesystem.

Backends (selected by ``settings.OBJECT_STORAGE_BACKEND``):
  - ``r2``    — Cloudflare R2 via the S3-compatible API (boto3). $0 egress.
  - ``s3``    — AWS S3 (or any S3-compatible store) via boto3.
  - ``local`` — write to disk under ``OBJECT_STORAGE_LOCAL_ROOT``; served by
                FastAPI ``StaticFiles`` at ``OBJECT_STORAGE_LOCAL_BASE_URL``.
                Dev fallback that needs no cloud credentials.

``r2`` and ``s3`` are the same boto3 client — only the endpoint URL and region
differ — so they share a code path. The public URL returned to callers (and
stored in the DB) always points at the CDN/base URL, never a raw bucket path.

Key layout convention (sharded first two hex chars to avoid hot partitions):
    products/{id[:2]}/{id}.jpg
    receipts/{id[:2]}/{id}.jpg
    catalog/{chain}/{sku}.jpg
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional, Protocol

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class ObjectStoreError(Exception):
    """Raised when an object cannot be stored or configuration is invalid."""


# Map common image content types to a file extension for key building.
_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def extension_for(content_type: str) -> str:
    """Return a bare file extension (no dot) for an image content type."""
    return _EXT_BY_CONTENT_TYPE.get((content_type or "").lower(), "jpg")


def product_image_key(product_id: str, content_type: str = "image/jpeg") -> str:
    """Build the storage key for a product image (sharded by id prefix)."""
    pid = str(product_id)
    shard = pid[:2] if len(pid) >= 2 else "00"
    return f"products/{shard}/{pid}.{extension_for(content_type)}"


class ObjectStore(Protocol):
    """Write-side interface every backend implements."""

    def put_object(self, key: str, data: bytes, content_type: str) -> str:
        """Persist ``data`` at ``key`` and return its public URL."""
        ...


class LocalObjectStore:
    """Filesystem backend for local development.

    Writes under ``root`` and returns a URL rooted at ``base_url`` (which the
    app serves via ``StaticFiles``). No cloud credentials required.
    """

    def __init__(self, root: str, base_url: str) -> None:
        self._root = root
        self._base_url = base_url.rstrip("/")

    def put_object(self, key: str, data: bytes, content_type: str) -> str:
        path = os.path.join(self._root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        logger.debug("Stored %d bytes at %s (local)", len(data), path)
        return f"{self._base_url}/{key}"


class S3ObjectStore:
    """S3-compatible backend (Cloudflare R2 or AWS S3) via boto3.

    R2 and S3 differ only by endpoint URL and region, so both use this class.
    The returned URL points at the configured public CDN base, not the raw
    (often private) bucket endpoint.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.R2_PUBLIC_BASE_URL:
            raise ObjectStoreError("R2_PUBLIC_BASE_URL must be set for the r2/s3 storage backend")
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ObjectStoreError(
                "boto3 is required for the r2/s3 storage backend; run `poetry install`"
            ) from exc

        endpoint = settings.R2_ENDPOINT_URL
        # Derive the R2 endpoint from the account id when not given explicitly.
        if not endpoint and settings.R2_ACCOUNT_ID:
            endpoint = f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

        self._bucket = settings.R2_BUCKET
        self._public_base = settings.R2_PUBLIC_BASE_URL.rstrip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=settings.R2_REGION,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            config=BotoConfig(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def put_object(self, key: str, data: bytes, content_type: str) -> str:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                # Long cache — objects at a key are immutable (id/sku based).
                CacheControl="public, max-age=31536000, immutable",
            )
        except Exception as exc:  # boto3 raises botocore ClientError subclasses
            raise ObjectStoreError(f"Failed to upload {key}: {exc}") from exc
        logger.debug("Stored %d bytes at %s (s3/r2)", len(data), key)
        return f"{self._public_base}/{key}"


def build_object_store(settings: Optional[Settings] = None) -> ObjectStore:
    """Construct the object store for the configured backend (uncached)."""
    settings = settings or get_settings()
    backend = (settings.OBJECT_STORAGE_BACKEND or "local").lower()

    if backend == "local":
        return LocalObjectStore(
            root=settings.OBJECT_STORAGE_LOCAL_ROOT,
            base_url=settings.OBJECT_STORAGE_LOCAL_BASE_URL,
        )
    if backend in ("r2", "s3"):
        return S3ObjectStore(settings)
    raise ObjectStoreError(f"Unknown OBJECT_STORAGE_BACKEND: {backend!r}")


@lru_cache(maxsize=1)
def get_object_store() -> ObjectStore:
    """Return a process-wide singleton object store for the active backend."""
    return build_object_store()
