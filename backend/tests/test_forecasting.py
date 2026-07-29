"""
Unit tests for app/analytics/forecasting.py.

Pure function over a pandas Series — no DB/network. Fixtures are
deterministic (formula-based, not random) so results are reproducible.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analytics.forecasting import (
    _candidates_for,
    forecast_series,
    is_intermittent,
)


def _monthly_series(values, start="2022-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="MS")
    return pd.Series(values, index=idx, name="qty")


def test_regular_seasonal_trend_series_uses_a_seasonal_or_trend_model():
    rng = np.arange(36)
    values = 50 + 0.8 * rng + 10 * np.sin(2 * np.pi * rng / 12)  # trend + yearly cycle
    series = _monthly_series(values)

    result = forecast_series(series, horizon=3)

    assert result["ok"] is True
    assert result["method"] in ("SARIMA", "Holt-Winters")
    assert result["intermittent_demand"] is False
    assert len(result["forecast"]) == 3
    assert len(result["confidence_interval_low"]) == 3
    assert len(result["confidence_interval_high"]) == 3
    # a real interval, not a degenerate point==point one
    assert all(lo <= f <= hi for lo, f, hi in zip(
        result["confidence_interval_low"], result["forecast"], result["confidence_interval_high"]
    ))


def test_sparse_series_is_detected_as_intermittent_and_offers_croston():
    values = np.zeros(24)
    values[[2, 6, 10, 14, 19, 23]] = [2, 3, 1, 4, 2, 3]

    assert is_intermittent(values) is True
    candidate_names = [name for name, _ in _candidates_for(len(values), 12, True)]
    assert "Croston" in candidate_names

    series = _monthly_series(values, start="2023-01-01")
    result = forecast_series(series, horizon=3, method="croston")
    assert result["ok"] is True
    assert result["method"] == "Croston"
    assert len(result["forecast"]) == 3
    assert all(v >= 0 for v in result["forecast"])

    # auto mode must always surface the detection, regardless of which
    # candidate happens to backtest best on such a short holdout window
    # (see forecasting.py's module notes on backtest noise) — this exact
    # fixture backtests best with Croston itself, so assert that too.
    auto_result = forecast_series(series, horizon=3)
    assert auto_result["intermittent_demand"] is True
    assert auto_result["method"] == "Croston"


def test_too_short_series_returns_insufficient_history_not_a_guess():
    series = _monthly_series([5.0, 7.0])  # only 2 points, below MIN_POINTS

    result = forecast_series(series, horizon=3)

    assert result["ok"] is False
    assert "reason" in result
    assert "forecast" not in result


def test_flat_zero_series_returns_insufficient_history():
    series = _monthly_series([0.0] * 12)  # plenty of periods, but no real activity

    result = forecast_series(series, horizon=3)

    assert result["ok"] is False
    assert result["nonzero_points"] == 0


def test_forecast_never_goes_negative_on_a_declining_series():
    series = _monthly_series([20, 15, 10, 5, 2, 0])

    result = forecast_series(series, horizon=3, method="linear")

    assert result["ok"] is True
    assert all(v >= 0 for v in result["forecast"])
    assert all(v >= 0 for v in result["confidence_interval_low"])


def test_unknown_method_raises_value_error():
    series = _monthly_series([1, 2, 3, 4, 5, 6])
    with pytest.raises(ValueError):
        forecast_series(series, horizon=3, method="prophet")
