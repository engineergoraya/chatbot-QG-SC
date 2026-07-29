"""
series.py — turn query result rows into a clean, regular pandas time series.

Pure function, no DB code: takes exactly what app/db/executor.py already
returns (a list of row dicts) plus which two columns are the date and the
value, and hands back a pandas Series indexed by a REGULAR calendar grid at
the requested frequency — sorted, with every missing period filled in.

Rows may arrive at finer granularity than the requested frequency (e.g. one
row per transaction date, forecasting monthly) — they are summed into their
containing period first, THEN the calendar is filled, so multiple rows in
the same month collapse into one point rather than each becoming its own
(wrongly irregular) index entry.

Missing periods default to 0 rather than NaN/dropped: for consumption-style
data (issuance, purchases), a period with no rows genuinely means zero
activity, not an unknown value — forecasting.py's models need that zero to
correctly detect intermittent demand and to not silently skip real gaps.
"""

from __future__ import annotations

import pandas as pd

# Friendly names accepted alongside raw pandas period-frequency codes
# ('D'/'W'/'M' etc. pass through unchanged via .get(freq, freq)).
_FREQ_ALIASES = {"daily": "D", "weekly": "W", "monthly": "M"}


def build_series(
    rows: list[dict],
    date_column: str,
    value_column: str,
    freq: str = "monthly",
    zero_fill: bool = True,
) -> pd.Series:
    """Build a regular, gap-filled time series from executor rows.

    Raises KeyError if a row is missing either column (fail closed — a
    forecast built on a silently-wrong column would be worse than an error).
    Returns an empty Series (dtype float64) when there is no usable data.
    """
    series_name = value_column
    if not rows:
        return pd.Series(dtype="float64", name=series_name)

    df = pd.DataFrame(rows)
    missing = [c for c in (date_column, value_column) if c not in df.columns]
    if missing:
        raise KeyError(f"rows are missing expected column(s): {missing}")

    dates = pd.to_datetime(df[date_column], errors="coerce")
    values = pd.to_numeric(df[value_column], errors="coerce")
    mask = dates.notna() & values.notna()
    dates, values = dates[mask], values[mask]
    if dates.empty:
        return pd.Series(dtype="float64", name=series_name)

    period_freq = _FREQ_ALIASES.get(freq, freq)
    periods = dates.dt.to_period(period_freq)

    grouped = values.groupby(periods).sum().sort_index()
    full_index = pd.period_range(grouped.index.min(), grouped.index.max(), freq=grouped.index.freq)
    series = grouped.reindex(full_index)
    if zero_fill:
        series = series.fillna(0.0)

    series.index = series.index.to_timestamp()
    series.index.name = date_column
    series.name = series_name
    return series
