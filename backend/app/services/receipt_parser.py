"""
Receipt photo parsing service using the Claude vision API.

Extracts structured product/price data from a photographed grocery receipt so
it can be turned into community price reports. The heavy lifting is delegated
to Claude's vision-capable model: we send the image plus a strict
JSON-output prompt and parse the response.

Design notes
------------
- The Anthropic client is injected (not constructed here) so tests can pass a
  mock and production can share one configured client.
- ``parse_receipt_image`` is defensive: it validates the MIME type, guards
  against oversized payloads, and raises :class:`ReceiptParseError` with a
  clear message on any failure rather than leaking SDK exceptions.
- The model is instructed to return *only* JSON. We still parse tolerantly
  (stripping markdown fences) because vision models occasionally wrap output.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Supported image MIME types for receipts.
SUPPORTED_IMAGE_TYPES: set[str] = {"image/jpeg", "image/png", "image/webp"}

# Max receipt image size (5 MB) — matches the upload endpoint's limit.
MAX_IMAGE_BYTES: int = 5 * 1024 * 1024

# Claude model used for vision parsing.
CLAUDE_VISION_MODEL: str = "claude-sonnet-4-5"

# Prompt instructing Claude to extract receipt line items as strict JSON.
RECEIPT_PROMPT: str = (
    "You are a receipt-parsing assistant for an Australian grocery app. "
    "Extract every purchased product line item from this receipt image. "
    "Return ONLY a JSON object (no prose, no markdown) with this exact shape:\n"
    "{\n"
    '  "store_name": string | null,\n'
    '  "receipt_date": string | null,   // ISO 8601 (YYYY-MM-DD) if visible\n'
    '  "total": number | null,           // total in dollars\n'
    '  "products": [\n'
    '    {"name": string, "price": number, "quantity": number, "unit_price": number | null}\n'
    "  ]\n"
    "}\n"
    "Prices are in AUD dollars (e.g. 3.50). If a field is not visible, use null. "
    "Do not invent products. If the image is not a receipt, return an empty products list."
)


class ReceiptParseError(ValueError):
    """Raised when a receipt image cannot be parsed into product data."""


@dataclass
class ParsedProduct:
    """A single product line item extracted from a receipt."""

    name: str
    price: float
    quantity: int = 1
    unit_price: Optional[float] = None


@dataclass
class ParsedReceipt:
    """Structured result of parsing a receipt image."""

    products: list[ParsedProduct] = field(default_factory=list)
    store_name: Optional[str] = None
    receipt_date: Optional[str] = None
    total: Optional[float] = None
    parsed_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON responses."""
        return {
            "products": [
                {
                    "name": p.name,
                    "price": p.price,
                    "quantity": p.quantity,
                    "unit_price": p.unit_price,
                }
                for p in self.products
            ],
            "store_name": self.store_name,
            "receipt_date": self.receipt_date,
            "total": self.total,
            "parsed_at": self.parsed_at,
        }


def _strip_json_fences(text: str) -> str:
    """Remove ```json ... ``` markdown fences a model may wrap output in."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


class ReceiptParser:
    """Parses grocery receipt images via the Claude vision API."""

    def __init__(self, anthropic_client: Any, *, model: str = CLAUDE_VISION_MODEL):
        """
        Args:
            anthropic_client: A configured Anthropic client (or a compatible
                mock in tests). Must expose ``messages.create(...)``.
            model: Claude model id to use for vision parsing.
        """
        self.client = anthropic_client
        self.model = model

    def _validate_image(self, image_bytes: bytes, image_type: str) -> None:
        """Validate image type and size, raising ReceiptParseError on failure."""
        if image_type not in SUPPORTED_IMAGE_TYPES:
            raise ReceiptParseError(
                f"Unsupported image type '{image_type}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_IMAGE_TYPES))}"
            )
        if not image_bytes:
            raise ReceiptParseError("Empty image payload")
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise ReceiptParseError(
                f"Image too large ({len(image_bytes)} bytes); " f"max is {MAX_IMAGE_BYTES} bytes"
            )

    def _build_message(self, image_bytes: bytes, image_type: str) -> list[dict]:
        """Build the Anthropic messages payload with the base64 image."""
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": RECEIPT_PROMPT},
                ],
            }
        ]

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Pull the text content out of an Anthropic messages response."""
        # SDK returns an object with ``.content`` being a list of blocks.
        content = getattr(response, "content", None)
        if content is None and isinstance(response, dict):
            content = response.get("content")
        if not content:
            raise ReceiptParseError("Empty response from vision model")
        # Concatenate all text blocks.
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(text)
        if not parts:
            raise ReceiptParseError("No text content in vision model response")
        return "".join(parts)

    def _parse_payload(self, raw_text: str) -> ParsedReceipt:
        """Parse the model's JSON text into a ParsedReceipt."""
        cleaned = _strip_json_fences(raw_text)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ReceiptParseError(f"Vision model did not return valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ReceiptParseError("Vision model JSON was not an object")

        products: list[ParsedProduct] = []
        for item in data.get("products", []) or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            price = item.get("price")
            if not name or price is None:
                # Skip incomplete line items rather than failing the whole parse.
                continue
            try:
                products.append(
                    ParsedProduct(
                        name=str(name).strip(),
                        price=float(price),
                        quantity=int(item.get("quantity") or 1),
                        unit_price=(
                            float(item["unit_price"])
                            if item.get("unit_price") is not None
                            else None
                        ),
                    )
                )
            except (TypeError, ValueError):
                logger.warning("Skipping malformed receipt line item: %r", item)
                continue

        total = data.get("total")
        return ParsedReceipt(
            products=products,
            store_name=(str(data["store_name"]) if data.get("store_name") else None),
            receipt_date=(str(data["receipt_date"]) if data.get("receipt_date") else None),
            total=float(total) if total is not None else None,
        )

    async def parse_receipt_image(self, image_bytes: bytes, image_type: str) -> ParsedReceipt:
        """
        Extract product details from a receipt photo via Claude vision.

        Args:
            image_bytes: Raw image bytes (JPEG/PNG/WebP).
            image_type: MIME type of the image.

        Returns:
            A :class:`ParsedReceipt` with any extracted products. An image that
            is valid but contains no recognizable products yields an empty
            ``products`` list (not an error).

        Raises:
            ReceiptParseError: If the image is invalid, the API call fails, or
                the response cannot be parsed.
        """
        self._validate_image(image_bytes, image_type)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                messages=self._build_message(image_bytes, image_type),
            )
        except Exception as exc:  # noqa: BLE001 - normalize all SDK errors
            logger.error("Claude vision API call failed: %s", exc)
            raise ReceiptParseError(f"Vision API request failed: {exc}") from exc

        raw_text = self._extract_text(response)
        parsed = self._parse_payload(raw_text)
        logger.info(
            "Parsed receipt: %d product(s), store=%r",
            len(parsed.products),
            parsed.store_name,
        )
        return parsed
