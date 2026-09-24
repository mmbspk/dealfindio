"""
Receipt photo upload + OCR router.

Implements ``POST /api/v1/receipts/upload``: accepts a photographed grocery
receipt, extracts line items via the Claude vision API
(:mod:`app.services.receipt_parser`), matches each item to a known product
(GTIN or fuzzy name), and creates ``source='receipt_ocr'`` price reports for
the matches. Each created report is enqueued for the same community-report
validation task used by manual submissions.

Line items that cannot be matched to a product are returned in
``unmatched_items`` so the client can prompt for a manual lookup, rather than
failing the whole upload.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_anthropic_client, get_current_user, get_db
from app.models import PriceReport, Product, Store, UserProfile
from app.models.user import User
from app.schemas.receipt import (
    CreatedReceiptReport,
    ExtractedProduct,
    ExtractedReceipt,
    ReceiptUploadResponse,
    UnmatchedReceiptItem,
)
from app.services.receipt_parser import (
    MAX_IMAGE_BYTES,
    SUPPORTED_IMAGE_TYPES,
    ParsedReceipt,
    ReceiptParseError,
    ReceiptParser,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Receipts"])

# Initial confidence for OCR-extracted reports (validator will refine).
RECEIPT_OCR_CONFIDENCE: float = 0.7


def _resolve_product(db: Session, item: ExtractedProduct) -> Product | None:
    """Match a receipt line item to a known product by fuzzy name."""
    # Receipts rarely carry GTINs, so we match on name. ``ilike`` mirrors the
    # lookup used by the manual price-report endpoint.
    return db.execute(
        select(Product).where(Product.name.ilike(f"%{item.name}%"))
    ).scalar_one_or_none()


@router.post(
    "/upload",
    response_model=ReceiptUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_receipt(
    file: UploadFile = File(..., description="Receipt image (JPEG/PNG/WebP)"),
    store_id: str | None = Form(None, description="Store UUID the receipt is from"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    anthropic_client=Depends(get_anthropic_client),
) -> ReceiptUploadResponse:
    """
    Upload a receipt photo, extract products via Claude vision, and create
    price reports for any matched products.

    Args:
        file: The receipt image upload.
        store_id: Optional store UUID; required to create price reports.
        db: Database session.
        current_user: Authenticated user (reporter).
        anthropic_client: Injected Anthropic client.

    Returns:
        A :class:`ReceiptUploadResponse` describing extracted data, created
        reports, and any unmatched line items.

    Raises:
        HTTPException: 400 for invalid/oversized files, 422 for unparseable
            images, 404 for an unknown store.
    """
    # --- validate file type ---
    if file.content_type not in SUPPORTED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported file type '{file.content_type}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_IMAGE_TYPES))}"
            ),
        )

    image_bytes = await file.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large; max is {MAX_IMAGE_BYTES // (1024 * 1024)}MB",
        )

    # --- resolve reporter profile ---
    user_profile = db.execute(
        select(UserProfile).where(UserProfile.firebase_uid == current_user.firebase_uid)
    ).scalar_one_or_none()
    if not user_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found. Please create profile first.",
        )

    # --- resolve store (needed to attach price reports) ---
    store: Store | None = None
    if store_id:
        # store_id arrives as a form string; parse to UUID so the query works
        # across dialects (SQLite rejects a bare str against a UUID column).
        try:
            store_uuid = UUID(store_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid store_id: {store_id}",
            )
        store = db.execute(
            select(Store).where(Store.id == store_uuid)
        ).scalar_one_or_none()
        if not store:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Store {store_id} not found",
            )

    # --- parse the receipt image ---
    parser = ReceiptParser(anthropic_client)
    try:
        parsed: ParsedReceipt = await parser.parse_receipt_image(
            image_bytes, file.content_type
        )
    except ReceiptParseError as exc:
        logger.warning("Receipt parse failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not parse receipt: {exc}",
        ) from exc

    parsed.parsed_at = datetime.now(timezone.utc).isoformat()

    extracted = ExtractedReceipt(
        products=[
            ExtractedProduct(
                name=p.name,
                price=p.price,
                quantity=p.quantity,
                unit_price=p.unit_price,
            )
            for p in parsed.products
        ],
        store_name=parsed.store_name,
        receipt_date=parsed.receipt_date,
        total=parsed.total,
        parsed_at=parsed.parsed_at,
    )

    created: list[CreatedReceiptReport] = []
    unmatched: list[UnmatchedReceiptItem] = []

    # Without a store we cannot create reports; return extraction only.
    if store is None:
        for item in extracted.products:
            unmatched.append(
                UnmatchedReceiptItem(
                    name=item.name,
                    price=item.price,
                    reason="No store_id provided; cannot create price report",
                )
            )
        return ReceiptUploadResponse(
            status="partial",
            extracted_data=extracted,
            created_reports=[],
            unmatched_items=unmatched,
            message=(
                f"Extracted {len(extracted.products)} product(s). "
                "Provide a store_id to create price reports."
            ),
        )

    # --- match each line item and create a price report ---
    for item in extracted.products:
        product = _resolve_product(db, item)
        if not product:
            unmatched.append(
                UnmatchedReceiptItem(
                    name=item.name,
                    price=item.price,
                    reason="No matching product found",
                )
            )
            continue

        price_report = PriceReport(
            product_id=product.id,
            store_id=store.id,
            price_cents=int(round(item.price * 100)),
            source="receipt_ocr",
            reporter_user_id=user_profile.id,
            confidence=RECEIPT_OCR_CONFIDENCE,
            is_verified=False,
            is_flagged=False,
            lat=store.lat,
            lon=store.lon,
        )
        db.add(price_report)
        db.flush()  # obtain ID without committing

        created.append(
            CreatedReceiptReport(
                price_report_id=price_report.id,
                product_id=product.id,
                product_name=product.name,
                confidence=RECEIPT_OCR_CONFIDENCE,
            )
        )

    db.commit()

    # --- enqueue validation for each created report ---
    for report in created:
        try:
            celery_app.send_task(
                "app.workers.report_validator.validate_community_report",
                args=[str(report.price_report_id)],
            )
        except Exception as exc:  # noqa: BLE001 - non-fatal; report already saved
            logger.error(
                "Failed to enqueue validation for report %s: %s",
                report.price_report_id,
                exc,
            )

    total_items = len(extracted.products)
    status_str = "success" if not unmatched else "partial"
    message = (
        f"Extracted {total_items} product(s). "
        f"{len(created)} matched, {len(unmatched)} require manual lookup."
    )
    logger.info(
        "Receipt upload by user %s: %d created, %d unmatched",
        user_profile.id,
        len(created),
        len(unmatched),
    )

    return ReceiptUploadResponse(
        status=status_str,
        extracted_data=extracted,
        created_reports=created,
        unmatched_items=unmatched,
        message=message,
    )
