"""
Prophet-based price prediction pipeline.

Loads price history from the DB, fits a weekly time-series model per
(product, chain) pair, and upserts PricePrediction rows for the next
FORECAST_WEEKS weeks.

Designed to run inside the `refit_and_predict_all` Celery task but has
no Celery / FastAPI dependencies — importable for unit tests.
"""

import logging
import math
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pandas as pd
from prophet import Prophet
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MIN_OBSERVATIONS = 8       # skip a product/chain pair below this
FORECAST_WEEKS = 2         # how many future weeks to predict
SIGNIFICANT_DROP_PCT = 0.05  # 5% below recent median → is_likely_sale


def _upsert_prediction(
    db: Session,
    product_id: UUID,
    chain: str | None,
    prediction_week: date,
    predicted_price_cents: int,
    ci_low_cents: int | None,
    ci_high_cents: int | None,
    is_likely_sale: bool,
    model_version: str,
) -> None:
    """Insert or update a PricePrediction row using SQLAlchemy dialect-aware upsert."""
    from app.models import PricePrediction

    existing = db.execute(
        select(PricePrediction).where(
            PricePrediction.product_id == product_id,
            PricePrediction.chain == chain,
            PricePrediction.prediction_week == datetime.combine(prediction_week, datetime.min.time()).replace(tzinfo=timezone.utc),
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if existing:
        existing.predicted_price_cents = predicted_price_cents
        existing.confidence_interval_low = ci_low_cents
        existing.confidence_interval_high = ci_high_cents
        existing.is_likely_sale = is_likely_sale
        existing.model_version = model_version
        existing.generated_at = now
    else:
        from app.models import PricePrediction
        pred = PricePrediction(
            product_id=product_id,
            chain=chain,
            prediction_week=datetime.combine(prediction_week, datetime.min.time()).replace(tzinfo=timezone.utc),
            predicted_price_cents=predicted_price_cents,
            confidence_interval_low=ci_low_cents,
            confidence_interval_high=ci_high_cents,
            is_likely_sale=is_likely_sale,
            model_version=model_version,
        )
        db.add(pred)
    db.commit()


def load_price_series(
    db: Session,
    product_id: UUID,
    chain: str,
    days: int = 180,
) -> pd.DataFrame:
    """
    Query price_reports for a (product, chain) pair and return a Prophet-
    compatible DataFrame with columns ds (datetime) and y (price in AUD).
    """
    from app.models import PriceReport, Store

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = db.execute(
        select(PriceReport.created_at, PriceReport.price_cents)
        .join(Store, PriceReport.store_id == Store.id)
        .where(
            PriceReport.product_id == product_id,
            Store.chain == chain,
            PriceReport.created_at >= cutoff,
        )
        .order_by(PriceReport.created_at)
    ).all()

    if not rows:
        return pd.DataFrame(columns=["ds", "y"])

    df = pd.DataFrame(rows, columns=["ds", "y"])
    df["ds"] = pd.to_datetime(df["ds"], utc=True).dt.tz_localize(None)
    df["y"] = df["y"] / 100.0  # cents → AUD
    # Weekly median to reduce noise from multiple stores per day
    df = df.set_index("ds").resample("W")["y"].median().dropna().reset_index()
    df.columns = ["ds", "y"]
    return df


def fit_and_forecast(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fit a Prophet model on the price series and return a forecast DataFrame.
    The returned DataFrame contains the future dates only (not historical),
    with columns: ds, yhat, yhat_lower, yhat_upper.
    """
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = Prophet(
            yearly_seasonality=False,
            weekly_seasonality=False,
            daily_seasonality=False,
            seasonality_mode="additive",
            changepoint_prior_scale=0.05,
            interval_width=0.80,
        )
        model.fit(df)

    future = model.make_future_dataframe(periods=FORECAST_WEEKS, freq="W")
    forecast = model.predict(future)

    # Return only the future rows (beyond the training data)
    last_train_ds = df["ds"].max()
    future_fc = forecast[forecast["ds"] > last_train_ds][
        ["ds", "yhat", "yhat_lower", "yhat_upper"]
    ].copy()

    # Floor at 0 — prices can't be negative
    for col in ["yhat", "yhat_lower", "yhat_upper"]:
        future_fc[col] = future_fc[col].clip(lower=0.0)

    return future_fc.reset_index(drop=True)


def is_significant_drop(forecast_price: float, recent_median: float) -> bool:
    """Return True if forecast_price is at least SIGNIFICANT_DROP_PCT below recent_median."""
    if recent_median <= 0:
        return False
    return forecast_price < recent_median * (1.0 - SIGNIFICANT_DROP_PCT)


def _recent_median(df: pd.DataFrame, days: int = 30) -> float:
    """Return the median price from the most recent N days of the series."""
    if df.empty:
        return 0.0
    cutoff = df["ds"].max() - pd.Timedelta(days=days)
    recent = df[df["ds"] >= cutoff]["y"]
    if recent.empty:
        return float(df["y"].median())
    return float(recent.median())


def run_predictions_for_product(db: Session, product_id: UUID) -> list[dict]:
    """
    Fit Prophet models for each chain that has price history for this product,
    upsert PricePrediction rows, and return a list of result dicts for logging.
    """
    from app.models import PriceReport, Store

    # Find all chains with data for this product
    chains = db.execute(
        select(Store.chain)
        .join(PriceReport, PriceReport.store_id == Store.id)
        .where(PriceReport.product_id == product_id)
        .distinct()
    ).scalars().all()

    results = []
    model_version = f"prophet-1.1-{date.today().strftime('%Y%m%d')}"

    for chain in chains:
        try:
            df = load_price_series(db, product_id, chain)

            if len(df) < MIN_OBSERVATIONS:
                logger.debug(
                    f"Skipping {product_id}/{chain}: only {len(df)} observations "
                    f"(need {MIN_OBSERVATIONS})"
                )
                results.append({
                    "product_id": str(product_id),
                    "chain": chain,
                    "status": "skipped_insufficient_data",
                    "observations": len(df),
                })
                continue

            median_price = _recent_median(df)
            forecast = fit_and_forecast(df)

            weeks_written = 0
            for _, row in forecast.iterrows():
                pred_price = float(row["yhat"])
                pred_week = row["ds"].date()
                # Only write predictions for future Mondays
                # (make_future_dataframe with freq=W lands on Sundays; round to Monday)
                pred_week_monday = pred_week - timedelta(days=pred_week.weekday())

                ci_low = float(row["yhat_lower"]) if not math.isnan(row["yhat_lower"]) else None
                ci_high = float(row["yhat_upper"]) if not math.isnan(row["yhat_upper"]) else None
                sale_flag = is_significant_drop(pred_price, median_price)

                _upsert_prediction(
                    db=db,
                    product_id=product_id,
                    chain=chain,
                    prediction_week=pred_week_monday,
                    predicted_price_cents=round(pred_price * 100),
                    ci_low_cents=round(ci_low * 100) if ci_low is not None else None,
                    ci_high_cents=round(ci_high * 100) if ci_high is not None else None,
                    is_likely_sale=sale_flag,
                    model_version=model_version,
                )
                weeks_written += 1

            results.append({
                "product_id": str(product_id),
                "chain": chain,
                "status": "ok",
                "observations": len(df),
                "predictions_written": weeks_written,
            })

        except Exception as exc:
            logger.exception(f"Prediction failed for {product_id}/{chain}: {exc}")
            results.append({
                "product_id": str(product_id),
                "chain": chain,
                "status": "error",
                "error": str(exc),
            })

    return results
