"""
Embedding service for semantic product search (Phase B).

Uses all-MiniLM-L6-v2 (384-dim, ~80MB) — fast enough for real-time query
encoding (<5ms CPU) while being meaningfully better than BM25 for intent
queries like "cheap protein snack" or "dairy-free milk alternative".

The model is loaded once at process startup and shared across all requests.
Celery workers each load their own copy.
"""

from __future__ import annotations

import re
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_MODEL_NAME = "all-MiniLM-L6-v2"
_EMBEDDING_DIM = 384

_model_lock = threading.Lock()
_model: "SentenceTransformer | None" = None


def get_model() -> "SentenceTransformer":
    """Return the shared SentenceTransformer instance (lazy, thread-safe)."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(_MODEL_NAME)
    return _model


# Unit normalisation table — maps common grocery size suffixes to a canonical form
# so "2L milk" and "2000ml milk" land close in embedding space.
_UNIT_MAP = {
    r"\b(\d+(?:\.\d+)?)\s*ml\b": lambda m: f"{float(m.group(1)) / 1000:.3g}l",
    r"\b(\d+(?:\.\d+)?)\s*g\b": lambda m: f"{float(m.group(1)) / 1000:.3g}kg",
}


def normalise_product_name(name: str) -> str:
    """Lowercase + unit-normalise a product name before embedding."""
    text = name.lower().strip()
    for pattern, replacement in _UNIT_MAP.items():
        text = re.sub(pattern, replacement, text)
    # Collapse whitespace
    return re.sub(r"\s+", " ", text)


def embed_text(text: str) -> list[float]:
    """Encode a single string to a 384-dim unit-normalised vector."""
    model = get_model()
    vec = model.encode(normalise_product_name(text), normalize_embeddings=True)
    return vec.tolist()


def embed_batch(texts: list[str], batch_size: int = 256) -> list[list[float]]:
    """Encode a list of strings. Used by the backfill Celery task."""
    model = get_model()
    normalised = [normalise_product_name(t) for t in texts]
    vecs = model.encode(normalised, normalize_embeddings=True, batch_size=batch_size)
    return [v.tolist() for v in vecs]
