"""
Unit tests for app/analytics/series.py.

Pure function, no DB/network — rows are plain dicts, same shape
app/db/executor.py already returns.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.analytics.series import build_series


def test_sums_rows_within_the_same_period_and_gap_fills_zero():
    rows = [
        {"period": "2025-01-15", "qty": 10},
        {"period": "2025-01-20", "qty": 5},
        {"period": "2025-03-01", "qty": 8},
    ]
    series = build_series(rows, "period", "qty", freq="monthly")

    assert list(series.index) == list(pd.date_range("2025-01-01", "2025-03-01", freq="MS"))
    assert series.loc["2025-01-01"] == 15.0   # 10 + 5 summed into January
    assert series.loc["2025-02-01"] == 0.0     # gap-filled, not dropped
    assert series.loc["2025-03-01"] == 8.0


def test_empty_rows_returns_empty_series():
    series = build_series([], "period", "qty")
    assert series.empty
    assert series.dtype.kind == "f"


def test_missing_column_raises_keyerror():
    with pytest.raises(KeyError):
        build_series([{"other": 1}], "period", "qty")


def test_unparseable_dates_and_values_are_dropped_not_crashed():
    rows = [
        {"period": "not-a-date", "qty": 5},
        {"period": "2025-02-01", "qty": "not-a-number"},
        {"period": "2025-02-01", "qty": 7},
    ]
    series = build_series(rows, "period", "qty", freq="monthly")
    assert len(series) == 1
    assert series.loc["2025-02-01"] == 7.0


def test_daily_frequency_gap_fills_missing_days():
    rows = [
        {"period": "2025-01-01", "qty": 3},
        {"period": "2025-01-04", "qty": 2},
    ]
    series = build_series(rows, "period", "qty", freq="daily")
    assert len(series) == 4  # Jan 1,2,3,4
    assert series.loc["2025-01-02"] == 0.0
    assert series.loc["2025-01-03"] == 0.0


def test_zero_fill_can_be_disabled():
    rows = [
        {"period": "2025-01-01", "qty": 3},
        {"period": "2025-03-01", "qty": 2},
    ]
    series = build_series(rows, "period", "qty", freq="monthly", zero_fill=False)
    assert series.isna().sum() == 1  # February left as NaN, not 0
