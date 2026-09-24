"""
ALDI Australia flyer scraper using PDF extraction with OCR fallback.

Finds the current ALDI catalogue PDF, downloads it, and extracts product
information using pdfplumber (text extraction) with pytesseract OCR fallback
for scanned/image-based PDFs.

Source: https://www.aldi.com.au/en/specials/
"""

import io
import logging
import re
from datetime import date, timedelta
from typing import Optional

import httpx
import pdfplumber
import redis
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from .base import ScrapedProduct, Scraper, ScraperError

logger = logging.getLogger(__name__)


class ALDIScraper(Scraper):
    """
    Scraper for ALDI Australia specials using PDF catalogues.

    Navigates to ALDI specials page to find the current week's PDF catalogue,
    downloads it, and extracts product information. Falls back to OCR
    (pytesseract) if text extraction fails.

    Attributes:
        ALDI_SPECIALS_URL: URL to ALDI specials landing page
        REQUEST_TIMEOUT_S: HTTP request timeout in seconds
        PDF_TIMEOUT_S: Timeout for PDF download
        USER_AGENT: HTTP User-Agent header
    """

    ALDI_SPECIALS_URL = "https://www.aldi.com.au/en/specials/"
    REQUEST_TIMEOUT_S = 10.0
    PDF_TIMEOUT_S = 30.0
    USER_AGENT = "dealfindio/1.0"

    def __init__(
        self,
        db_session: Optional[Session] = None,
        redis_client: Optional[redis.Redis] = None,
    ):
        """Initialize ALDI scraper."""
        super().__init__(db_session, redis_client)
        self.logger = logging.getLogger(__name__)

    async def discover_source(self) -> str:
        """
        Fetch ALDI specials page and extract link to current week's PDF.

        Navigates to the ALDI specials page and looks for a link to the
        current catalogue PDF (usually named something like "Catalogue-WEEK.pdf"
        or "specials-WEEK.pdf").

        Returns:
            Full URL to the current ALDI catalogue PDF

        Raises:
            ScraperError: If page cannot be fetched or PDF link not found
        """
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                self.logger.debug(f"Fetching ALDI specials page: {self.ALDI_SPECIALS_URL}")
                response = await client.get(
                    self.ALDI_SPECIALS_URL,
                    headers={"User-Agent": self.USER_AGENT},
                    timeout=self.REQUEST_TIMEOUT_S,
                )
                response.raise_for_status()

            # Parse HTML to find PDF link
            soup = BeautifulSoup(response.text, "html.parser")

            # Look for PDF link (usually in href attribute)
            # Try multiple patterns
            pdf_link = soup.find("a", href=re.compile(r".*\.pdf", re.I))

            if not pdf_link:
                # Try data attributes or aria labels
                pdf_link = soup.find(
                    "a",
                    attrs={"aria-label": re.compile(r"(catalogue|specials|weekly|flyer)", re.I)},
                )

            if not pdf_link:
                raise ScraperError("ALDI PDF catalogue link not found on specials page")

            pdf_url = pdf_link.get("href")
            if not pdf_url:
                raise ScraperError("PDF link found but href is empty")

            # Make absolute URL if needed
            if not pdf_url.startswith("http"):
                base_url = "https://www.aldi.com.au"
                pdf_url = base_url.rstrip("/") + "/" + pdf_url.lstrip("/")

            self.logger.info(f"Found ALDI PDF catalogue: {pdf_url}")
            return pdf_url

        except httpx.HTTPStatusError as e:
            raise ScraperError(
                f"HTTP error fetching ALDI specials page: {e.response.status_code}"
            ) from e
        except httpx.TimeoutException as e:
            raise ScraperError(f"Timeout fetching ALDI specials page: {e}") from e
        except Exception as e:
            raise ScraperError(f"Error discovering ALDI PDF: {e}") from e

    async def fetch_flyer(self, source: str) -> bytes:
        """
        Download ALDI PDF catalogue from the source URL.

        Args:
            source: Full URL to ALDI PDF catalogue

        Returns:
            PDF file content as bytes

        Raises:
            ScraperError: On network errors, invalid PDF, etc.
        """
        try:
            async with httpx.AsyncClient() as client:
                self.logger.debug(f"Downloading ALDI PDF: {source}")
                response = await client.get(
                    source,
                    headers={"User-Agent": self.USER_AGENT},
                    timeout=self.PDF_TIMEOUT_S,
                )
                response.raise_for_status()

            pdf_bytes = response.content
            self.logger.info(f"Successfully downloaded ALDI PDF: {len(pdf_bytes)} bytes")
            return pdf_bytes

        except httpx.HTTPStatusError as e:
            raise ScraperError(f"HTTP error downloading PDF: {e.response.status_code}") from e
        except httpx.TimeoutException as e:
            raise ScraperError(f"Timeout downloading PDF: {e}") from e
        except Exception as e:
            raise ScraperError(f"Error downloading PDF: {e}") from e

    async def parse_flyer(self, flyer_data: bytes) -> list[ScrapedProduct]:
        """
        Parse ALDI PDF and extract product information.

        Uses pdfplumber for text extraction (fast, works for native text PDFs).
        Falls back to pytesseract OCR if text extraction fails or yields little text
        (for scanned PDFs).

        Expected format: Product names and prices are typically formatted as:
        - "Product Name $X.XX" (on same line)
        - Or separate lines with product name followed by price

        Args:
            flyer_data: PDF file content as bytes

        Returns:
            List of ScrapedProduct objects extracted from the PDF

        Raises:
            ScraperError: If both text extraction and OCR fail
        """
        products = []

        # Try text extraction first (works for native text PDFs)
        try:
            self.logger.debug("Attempting PDF text extraction with pdfplumber")
            with pdfplumber.open(io.BytesIO(flyer_data)) as pdf:
                total_pages = len(pdf.pages)
                self.logger.info(f"PDF has {total_pages} pages")

                for page_num, page in enumerate(pdf.pages):
                    try:
                        text = page.extract_text()
                        if not text:
                            self.logger.debug(
                                f"Page {page_num}: No text extracted " "(likely scanned image)"
                            )
                            continue

                        self.logger.debug(f"Page {page_num}: Extracted {len(text)} chars of text")

                        # Parse text for prices
                        lines = text.split("\n")
                        for i, line in enumerate(lines):
                            line = line.strip()
                            if not line:
                                continue

                            # Match price pattern: $X.XX or $XX.XX or $XX.XX+
                            price_match = re.search(r"\$(\d+\.?\d{0,2})", line)
                            if price_match:
                                price_str = price_match.group(1)
                                try:
                                    price_cents = int(float(price_str) * 100)
                                except ValueError:
                                    continue

                                # Extract product name (usually before price)
                                product_name = line[: price_match.start()].strip()
                                if not product_name:
                                    # Try to get name from previous line
                                    product_name = lines[i - 1].strip() if i > 0 else "Unknown"

                                if product_name and product_name != "Unknown":
                                    product = ScrapedProduct(
                                        product_name=product_name,
                                        price_cents=price_cents,
                                        valid_from=date.today(),
                                        valid_to=date.today() + timedelta(days=7),
                                    )
                                    products.append(product)
                                    self.logger.debug(
                                        f"Page {page_num}: Parsed '{product_name}' "
                                        f"- ${price_cents/100:.2f}"
                                    )

                    except Exception as e:
                        self.logger.warning(f"Error processing page {page_num}: {e}")
                        continue

        except Exception as e:
            self.logger.warning(f"pdfplumber text extraction failed: {e}")

        # If text extraction yielded few products, try OCR fallback
        if len(products) < 5:
            self.logger.info(
                f"Text extraction only found {len(products)} products. " "Trying OCR fallback..."
            )
            try:
                import pytesseract
                from pdf2image import convert_from_bytes
                from PIL import Image  # noqa: F401

                self.logger.debug("Converting PDF to images for OCR")
                images = convert_from_bytes(flyer_data)

                for img_num, img in enumerate(images):
                    try:
                        self.logger.debug(
                            f"Running OCR on page {img_num} " f"({img.width}x{img.height})"
                        )
                        text = pytesseract.image_to_string(img)

                        if not text:
                            self.logger.debug(f"Page {img_num}: OCR returned no text")
                            continue

                        # Re-use text parsing logic
                        lines = text.split("\n")
                        for i, line in enumerate(lines):
                            line = line.strip()
                            if not line:
                                continue

                            price_match = re.search(r"\$(\d+\.?\d{0,2})", line)
                            if price_match:
                                price_str = price_match.group(1)
                                try:
                                    price_cents = int(float(price_str) * 100)
                                except ValueError:
                                    continue

                                product_name = line[: price_match.start()].strip()
                                if not product_name and i > 0:
                                    product_name = lines[i - 1].strip()

                                if product_name:
                                    product = ScrapedProduct(
                                        product_name=product_name,
                                        price_cents=price_cents,
                                        valid_from=date.today(),
                                        valid_to=date.today() + timedelta(days=7),
                                    )
                                    products.append(product)

                    except Exception as e:
                        self.logger.warning(f"OCR processing failed for page {img_num}: {e}")
                        continue

            except ImportError:
                self.logger.warning(
                    "pytesseract or pdf2image not installed. OCR fallback unavailable."
                )
            except Exception as e:
                self.logger.warning(f"OCR fallback failed: {e}")

        if not products:
            self.logger.warning("No products extracted from ALDI PDF")

        self.logger.info(f"Successfully parsed {len(products)} products from ALDI PDF")
        return products
