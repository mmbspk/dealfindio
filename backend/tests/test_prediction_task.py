"""
Tests for the ML Celery task (refit_and_predict_all).

Patches are applied at the source modules because the task uses
local imports inside the function body.
"""

from unittest.mock import MagicMock, patch


def _make_mock_session(products):
    mock_session = MagicMock()
    mock_session.query.return_value.all.return_value = products
    return mock_session


def test_refit_and_predict_all_returns_required_keys():
    """Task returns all required MLResult keys and a non-pending status."""
    mock_results = [
        {"product_id": "abc", "chain": "coles", "status": "ok", "observations": 12, "predictions_written": 2}
    ]
    mock_product = MagicMock()
    mock_product.id = "product-uuid"
    mock_session = _make_mock_session([mock_product])

    with patch("app.deps._SessionLocal", return_value=mock_session), \
         patch("ml.predictor.run_predictions_for_product", return_value=mock_results), \
         patch("app.workers.notification_tasks.notify_prediction_drops") as mock_notify:

        from app.workers.ml_tasks import refit_and_predict_all
        result = refit_and_predict_all.apply().get()

    assert "products_refitted" in result
    assert "predictions_generated" in result
    assert "status" in result
    assert "duration_seconds" in result
    assert result["status"] != "pending_implementation"


def test_refit_and_predict_all_skips_errors_gracefully():
    """Task continues past individual product failures and returns partial/error status."""
    def raise_for_bad(session, product_id):
        raise ValueError("Simulated Prophet error")

    mock_product = MagicMock()
    mock_product.id = "bad-product-uuid"
    mock_session = _make_mock_session([mock_product])

    with patch("app.deps._SessionLocal", return_value=mock_session), \
         patch("ml.predictor.run_predictions_for_product", side_effect=raise_for_bad), \
         patch("app.workers.notification_tasks.notify_prediction_drops"):

        from app.workers.ml_tasks import refit_and_predict_all
        result = refit_and_predict_all.apply().get()

    assert result["products_refitted"] == 0
    assert result["status"] in ("error", "partial", "success")


def test_refit_and_predict_all_triggers_notification_task():
    """After predictions are generated, notify_prediction_drops.delay() is called."""
    mock_results = [{"status": "ok", "predictions_written": 2}]
    mock_product = MagicMock()
    mock_product.id = "product-uuid"
    mock_session = _make_mock_session([mock_product])

    with patch("app.deps._SessionLocal", return_value=mock_session), \
         patch("ml.predictor.run_predictions_for_product", return_value=mock_results), \
         patch("app.workers.notification_tasks.notify_prediction_drops") as mock_notify:

        from app.workers.ml_tasks import refit_and_predict_all
        refit_and_predict_all.apply().get()

    mock_notify.delay.assert_called_once()


def test_refit_no_notification_when_no_predictions():
    """notify_prediction_drops is NOT called when predictions_generated == 0."""
    mock_results = [{"status": "skipped_insufficient_data", "observations": 2}]
    mock_product = MagicMock()
    mock_product.id = "product-uuid"
    mock_session = _make_mock_session([mock_product])

    with patch("app.deps._SessionLocal", return_value=mock_session), \
         patch("ml.predictor.run_predictions_for_product", return_value=mock_results), \
         patch("app.workers.notification_tasks.notify_prediction_drops") as mock_notify:

        from app.workers.ml_tasks import refit_and_predict_all
        refit_and_predict_all.apply().get()

    mock_notify.delay.assert_not_called()
