"""
Unit tests for the reputation / confidence scoring service.

These test the pure business logic in :mod:`app.services.reputation` — tier
derivation, reputation clamping, upvote application, and per-report confidence
blending. No database or external services are required.
"""

import pytest

from app.services.reputation import (
    BRONZE_MAX,
    CONFIDENCE_WEIGHTS,
    DEFAULT_REPUTATION,
    LOW_REPUTATION_THRESHOLD,
    MAX_REPUTATION,
    SILVER_MAX,
    ConfidenceBreakdown,
    apply_upvote,
    calculate_tier,
    clamp_reputation,
    compute_confidence,
    is_low_reputation,
    reputation_factor,
)


class TestCalculateTier:
    @pytest.mark.unit
    def test_bronze_lower_bound(self):
        assert calculate_tier(0) == "bronze"

    @pytest.mark.unit
    def test_bronze_upper_bound(self):
        assert calculate_tier(BRONZE_MAX) == "bronze"

    @pytest.mark.unit
    def test_silver_lower_bound(self):
        assert calculate_tier(BRONZE_MAX + 1) == "silver"

    @pytest.mark.unit
    def test_silver_upper_bound(self):
        assert calculate_tier(SILVER_MAX) == "silver"

    @pytest.mark.unit
    def test_gold(self):
        assert calculate_tier(SILVER_MAX + 1) == "gold"
        assert calculate_tier(1000) == "gold"

    @pytest.mark.unit
    def test_negative_count_defaults_bronze(self):
        assert calculate_tier(-5) == "bronze"


class TestClampReputation:
    @pytest.mark.unit
    def test_within_range_unchanged(self):
        assert clamp_reputation(2.5) == 2.5

    @pytest.mark.unit
    def test_above_max_clamped(self):
        assert clamp_reputation(9.9) == MAX_REPUTATION

    @pytest.mark.unit
    def test_below_min_clamped(self):
        assert clamp_reputation(-3.0) == 0.0


class TestIsLowReputation:
    @pytest.mark.unit
    def test_below_threshold(self):
        assert is_low_reputation(LOW_REPUTATION_THRESHOLD - 0.01) is True

    @pytest.mark.unit
    def test_at_threshold_not_low(self):
        assert is_low_reputation(LOW_REPUTATION_THRESHOLD) is False


class TestReputationFactor:
    @pytest.mark.unit
    def test_zero(self):
        assert reputation_factor(0.0) == 0.0

    @pytest.mark.unit
    def test_max_maps_to_one(self):
        assert reputation_factor(MAX_REPUTATION) == 1.0

    @pytest.mark.unit
    def test_midpoint(self):
        assert reputation_factor(2.5) == pytest.approx(0.5)

    @pytest.mark.unit
    def test_above_max_capped_at_one(self):
        assert reputation_factor(99.0) == 1.0


class TestApplyUpvote:
    @pytest.mark.unit
    def test_single_upvote(self):
        assert apply_upvote(1.0, 1) == pytest.approx(1.01)

    @pytest.mark.unit
    def test_multiple_upvotes(self):
        assert apply_upvote(1.0, 5) == pytest.approx(1.05)

    @pytest.mark.unit
    def test_clamped_at_max(self):
        assert apply_upvote(MAX_REPUTATION, 10) == MAX_REPUTATION


class TestComputeConfidence:
    @pytest.mark.unit
    def test_weights_sum_to_one(self):
        assert sum(CONFIDENCE_WEIGHTS.values()) == pytest.approx(1.0)

    @pytest.mark.unit
    def test_clean_report_high_reputation(self):
        result = compute_confidence(MAX_REPUTATION)
        assert isinstance(result, ConfidenceBreakdown)
        # All factors at 1.0 -> confidence 1.0
        assert result.confidence == pytest.approx(1.0)

    @pytest.mark.unit
    def test_outlier_reduces_confidence(self):
        clean = compute_confidence(MAX_REPUTATION, is_outlier=False)
        outlier = compute_confidence(MAX_REPUTATION, is_outlier=True)
        assert outlier.confidence < clean.confidence
        # Outlier zeroes the 30% outlier weight.
        assert outlier.confidence == pytest.approx(
            clean.confidence - CONFIDENCE_WEIGHTS["outlier"]
        )

    @pytest.mark.unit
    def test_duplicate_reduces_confidence(self):
        clean = compute_confidence(MAX_REPUTATION, is_duplicate=False)
        dup = compute_confidence(MAX_REPUTATION, is_duplicate=True)
        assert dup.confidence == pytest.approx(
            clean.confidence - CONFIDENCE_WEIGHTS["duplicate"]
        )

    @pytest.mark.unit
    def test_new_user_reputation_capped(self):
        # New user with max reputation still capped at 0.5 reputation factor.
        result = compute_confidence(MAX_REPUTATION, is_new_user=True)
        assert result.reputation_factor == 0.5

    @pytest.mark.unit
    def test_confidence_never_exceeds_one(self):
        result = compute_confidence(999.0)
        assert 0.0 <= result.confidence <= 1.0

    @pytest.mark.unit
    def test_confidence_never_below_zero(self):
        result = compute_confidence(
            0.0, is_outlier=True, is_duplicate=True, is_new_user=True
        )
        assert result.confidence >= 0.0

    @pytest.mark.unit
    def test_default_reputation_is_neutral(self):
        # Sanity: a brand-new neutral user produces a mid-range confidence.
        result = compute_confidence(DEFAULT_REPUTATION)
        assert 0.5 <= result.confidence <= 0.9
