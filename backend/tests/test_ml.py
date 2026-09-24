"""
Tests for the ML predictor pipeline (ml/predictor.py).

Covers:
- is_significant_drop: exact threshold assertions
- fit_and_forecast: synthetic data → valid forecast shape and values
- load_price_series: SQLite-backed query → correct DataFrame shape
- GET /api/v1/predictions/{product_id}: API endpoint contract
"""

import math
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd

# ---------------------------------------------------------------------------
# is_significant_drop
# ---------------------------------------------------------------------------

def test_is_significant_drop_true():
    from ml.predictor import SIGNIFICANT_DROP_PCT, is_significant_drop
    median = 10.00
    # strictly below threshold — 6% below
    assert is_significant_drop(median * (1 - SIGNIFICANT_DROP_PCT - 0.01), median) is True


def test_is_significant_drop_false_just_above():
    from ml.predictor import SIGNIFICANT_DROP_PCT, is_significant_drop
    median = 10.00
    # 4.9% below — should not trigger
    assert is_significant_drop(median * (1 - SIGNIFICANT_DROP_PCT + 0.001), median) is False


def test_is_significant_drop_false_equal():
    from ml.predictor import is_significant_drop
    assert is_significant_drop(5.00, 5.00) is False


def test_is_significant_drop_false_above():
    from ml.predictor import is_significant_drop
    assert is_significant_drop(5.50, 5.00) is False


def test_is_significant_drop_zero_median():
    from ml.predictor import is_significant_drop
    # Zero median — guard against division by zero
    assert is_significant_drop(0.0, 0.0) is False


# ---------------------------------------------------------------------------
# fit_and_forecast
# ---------------------------------------------------------------------------

def _make_weekly_df(n: int, base_price: float = 5.00) -> pd.DataFrame:
    """Synthetic weekly price series."""
    start = datetime(2024, 1, 1)
    dates = [start + timedelta(weeks=i) for i in range(n)]
    prices = [base_price + (i % 3) * 0.20 for i in range(n)]
    return pd.DataFrame({"ds": dates, "y": prices})


def test_fit_and_forecast_returns_correct_shape():
    from ml.predictor import FORECAST_WEEKS, fit_and_forecast
    df = _make_weekly_df(12)
    forecast = fit_and_forecast(df)
    assert len(forecast) == FORECAST_WEEKS
    assert set(["ds", "yhat", "yhat_lower", "yhat_upper"]).issubset(forecast.columns)


def test_fit_and_forecast_values_are_finite():
    from ml.predictor import fit_and_forecast
    df = _make_weekly_df(12)
    forecast = fit_and_forecast(df)
    for col in ["yhat", "yhat_lower", "yhat_upper"]:
        assert all(math.isfinite(v) for v in forecast[col])


def test_fit_and_forecast_prices_non_negative():
    from ml.predictor import fit_and_forecast
    df = _make_weekly_df(12)
    forecast = fit_and_forecast(df)
    assert (forecast["yhat"] >= 0).all()
    assert (forecast["yhat_lower"] >= 0).all()


def test_fit_and_forecast_future_dates_only():
    from ml.predictor import fit_and_forecast
    df = _make_weekly_df(12)
    last_train = df["ds"].max()
    forecast = fit_and_forecast(df)
    assert (forecast["ds"] > last_train).all()


# ---------------------------------------------------------------------------
# load_price_series (needs DB fixtures)
# ---------------------------------------------------------------------------

def test_load_price_series_empty_when_no_data(test_db_session, sample_product, sample_store):
    from ml.predictor import load_price_series
    result = load_price_series(test_db_session, sample_product.id, "NonExistentChain")
    assert isinstance(result, pd.DataFrame)
    assert result.empty or list(result.columns) == ["ds", "y"]


def test_run_predictions_skips_when_insufficient_data(test_db_session, sample_product, sample_store):
    from app.models import PriceReport
    from ml.predictor import run_predictions_for_product

    now = datetime.now(timezone.utc)
    # Only 3 reports — below MIN_OBSERVATIONS (8)
    for i in range(3):
        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=300 + i * 10,
            source="flyer_coles",
            created_at=now - timedelta(weeks=i + 1),
        )
        test_db_session.add(report)
    test_db_session.commit()

    results = run_predictions_for_product(test_db_session, sample_product.id)
    assert len(results) > 0
    assert all(r["status"] == "skipped_insufficient_data" for r in results)


def test_run_predictions_no_data_returns_empty(test_db_session, sample_product):
    from ml.predictor import run_predictions_for_product

    results = run_predictions_for_product(test_db_session, sample_product.id)
    assert results == []


def test_load_price_series_returns_correct_columns(test_db_session, sample_product, sample_store):
    from app.models import PriceReport
    from ml.predictor import load_price_series

    # Seed enough price reports across different weeks
    now = datetime.now(timezone.utc)
    for i in range(10):
        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=350 + i * 5,
            source="flyer_coles",
            created_at=now - timedelta(weeks=i + 1),
        )
        test_db_session.add(report)
    test_db_session.commit()

    chain = sample_store.chain
    result = load_price_series(test_db_session, sample_product.id, chain)
    assert "ds" in result.columns
    assert "y" in result.columns
    assert len(result) > 0
    assert (result["y"] > 0).all()


# ---------------------------------------------------------------------------
# Predictions API endpoint
# ---------------------------------------------------------------------------

def test_predictions_endpoint_empty(test_client, sample_product):
    resp = test_client.get(f"/api/v1/predictions/{sample_product.id}")
    assert resp.status_code == 200
    assert resp.json() == []


def test_predictions_endpoint_returns_data(test_client, test_db_session, sample_product):
    from app.models import PricePrediction

    now = datetime.now(timezone.utc)
    pred = PricePrediction(
        product_id=sample_product.id,
        chain="coles",
        predicted_price_cents=349,
        prediction_week=now + timedelta(days=7),
        confidence_interval_low=320,
        confidence_interval_high=380,
        is_likely_sale=True,
        model_version="prophet-test",
    )
    test_db_session.add(pred)
    test_db_session.commit()

    resp = test_client.get(f"/api/v1/predictions/{sample_product.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["chain"] == "coles"
    assert abs(row["predicted_price_aud"] - 3.49) < 0.01
    assert row["is_likely_sale"] is True
    assert row["ci_low_aud"] is not None
    assert row["ci_high_aud"] is not None


def test_predictions_endpoint_404_unknown_product(test_client):
    resp = test_client.get(f"/api/v1/predictions/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_predictions_nested_alias(test_client, test_db_session, sample_product):
    from app.models import PricePrediction

    now = datetime.now(timezone.utc)
    pred = PricePrediction(
        product_id=sample_product.id,
        chain="woolworths",
        predicted_price_cents=299,
        prediction_week=now + timedelta(days=14),
        is_likely_sale=False,
        model_version="test",
    )
    test_db_session.add(pred)
    test_db_session.commit()

    resp = test_client.get(f"/api/v1/products/{sample_product.id}/predictions")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_predictions_excludes_past_weeks(test_client, test_db_session, sample_product):
    from app.models import PricePrediction

    now = datetime.now(timezone.utc)
    past = PricePrediction(
        product_id=sample_product.id,
        chain="coles",
        predicted_price_cents=399,
        prediction_week=now - timedelta(days=14),
        is_likely_sale=False,
        model_version="test",
    )
    future = PricePrediction(
        product_id=sample_product.id,
        chain="coles",
        predicted_price_cents=349,
        prediction_week=now + timedelta(days=7),
        is_likely_sale=True,
        model_version="test",
    )
    test_db_session.add_all([past, future])
    test_db_session.commit()

    resp = test_client.get(f"/api/v1/predictions/{sample_product.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["is_likely_sale"] is True
