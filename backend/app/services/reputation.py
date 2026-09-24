"""
Reputation and tier scoring service for dealfindio community reporters.

Centralizes the pure, side-effect-free logic used across the app to score
community reporters and derive their trust tier. Both the API layer
(``app.routers.users``) and the Celery validation layer
(``app.workers.report_validator``) import from here so the scoring rules live
in one place.

Reputation model
----------------
- ``reputation_score`` is on a 0.0-5.0 scale (higher = more trusted).
- New users start at ``DEFAULT_REPUTATION`` (1.0, neutral).
- Tier is derived from ``contribution_count`` (number of accepted reports).
- A per-report ``confidence`` (0.0-1.0) is computed at validation time from a
  weighted blend of reporter reputation, outlier detection, and duplicate
  checks.

These functions are intentionally pure (no DB, no I/O) so they are trivial to
unit-test and safe to call from both sync and async contexts.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants (single source of truth for scoring rules)
# ---------------------------------------------------------------------------

#: Reputation scale bounds.
MIN_REPUTATION: float = 0.0
MAX_REPUTATION: float = 5.0

#: Reputation assigned to a brand-new reporter (neutral).
DEFAULT_REPUTATION: float = 1.0

#: Below this reputation, a reporter is automatically flagged for review.
LOW_REPUTATION_THRESHOLD: float = 0.5

#: Tier thresholds, keyed on lifetime ``contribution_count``.
BRONZE_MAX: int = 10  # 0-10   -> bronze
SILVER_MAX: int = 50  # 11-50  -> silver
# 50+ -> gold

#: Confidence starts here before adjustments (per report, 0.0-1.0 scale).
INITIAL_CONFIDENCE: float = 0.5

#: Reputation earned per confirmed upvote on one of a user's reports.
UPVOTE_REPUTATION_DELTA: float = 0.01

#: Weights used to blend the per-report confidence score. Must sum to 1.0.
CONFIDENCE_WEIGHTS: dict[str, float] = {
    "reputation": 0.40,
    "outlier": 0.30,
    "duplicate": 0.20,
    "other": 0.10,
}


@dataclass
class ConfidenceBreakdown:
    """Structured breakdown of how a report's confidence score was derived."""

    reputation_factor: float
    outlier_factor: float
    duplicate_factor: float
    other_factor: float
    confidence: float


# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------


def calculate_tier(contribution_count: int) -> str:
    """
    Derive a reporter's trust tier from their lifetime contribution count.

    Tiers:
        bronze: 0-10 contributions
        silver: 11-50 contributions
        gold:   50+ contributions

    Args:
        contribution_count: Number of accepted price reports the user has made.

    Returns:
        One of ``"bronze"``, ``"silver"``, or ``"gold"``.
    """
    if contribution_count < 0:
        contribution_count = 0
    if contribution_count > SILVER_MAX:
        return "gold"
    if contribution_count > BRONZE_MAX:
        return "silver"
    return "bronze"


# ---------------------------------------------------------------------------
# Reputation helpers
# ---------------------------------------------------------------------------


def clamp_reputation(score: float) -> float:
    """Clamp a reputation score into the valid 0.0-5.0 range."""
    return max(MIN_REPUTATION, min(MAX_REPUTATION, score))


def is_low_reputation(score: float) -> bool:
    """Return True if a reputation score is below the auto-flag threshold."""
    return score < LOW_REPUTATION_THRESHOLD


def reputation_factor(reputation_score: float) -> float:
    """
    Normalize a 0.0-5.0 reputation score to a 0.0-1.0 confidence factor.

    Args:
        reputation_score: Reporter reputation on the 0.0-5.0 scale.

    Returns:
        A 0.0-1.0 factor suitable for weighting into a confidence score.
    """
    return max(0.0, min(1.0, reputation_score / MAX_REPUTATION))


def apply_upvote(reputation_score: float, votes: int = 1) -> float:
    """
    Apply upvote reputation gains, clamped to the valid range.

    Args:
        reputation_score: Current reputation on the 0.0-5.0 scale.
        votes: Number of upvotes to apply (default 1).

    Returns:
        The new, clamped reputation score.
    """
    return clamp_reputation(reputation_score + UPVOTE_REPUTATION_DELTA * votes)


# ---------------------------------------------------------------------------
# Per-report confidence scoring
# ---------------------------------------------------------------------------


def compute_confidence(
    reputation_score: float,
    *,
    is_outlier: bool = False,
    is_duplicate: bool = False,
    is_new_user: bool = False,
) -> ConfidenceBreakdown:
    """
    Compute a per-report confidence score (0.0-1.0) from validation signals.

    This is the pure core of the validation confidence calculation. The Celery
    ``validate_community_report`` task supplies the boolean signals it derives
    from the database (outlier detection against the 30-day rolling mean,
    duplicate detection, and reporter status), and this function blends them
    into a single confidence value using ``CONFIDENCE_WEIGHTS``.

    Args:
        reputation_score: Reporter reputation on the 0.0-5.0 scale.
        is_outlier: True if the reported price is a statistical outlier
            (>3 sigma from the 30-day rolling mean).
        is_duplicate: True if this looks like a duplicate of a recent report.
        is_new_user: True if the reporter has too little history to fully
            trust (caps the reputation factor at 0.5).

    Returns:
        A :class:`ConfidenceBreakdown` with the component factors and the
        final clamped confidence value.
    """
    rep = reputation_factor(reputation_score)
    if is_new_user:
        rep = min(rep, 0.5)

    # Outlier / duplicate signals reduce confidence toward zero when tripped.
    outlier = 0.0 if is_outlier else 1.0
    duplicate = 0.0 if is_duplicate else 1.0
    other = 1.0  # reserved for future signals (e.g. photo evidence)

    confidence = (
        rep * CONFIDENCE_WEIGHTS["reputation"]
        + outlier * CONFIDENCE_WEIGHTS["outlier"]
        + duplicate * CONFIDENCE_WEIGHTS["duplicate"]
        + other * CONFIDENCE_WEIGHTS["other"]
    )
    confidence = max(0.0, min(1.0, confidence))

    return ConfidenceBreakdown(
        reputation_factor=rep,
        outlier_factor=outlier,
        duplicate_factor=duplicate,
        other_factor=other,
        confidence=confidence,
    )
