"""
forecasting.py — backtested model selection for demand forecasting.

Design adapted from a teammate's backend/tools/forecast_tools.py (a
different, unrelated repo — see the ETL port commit for context): instead of
committing to one fixed model, build a small bank of candidates, backtest
each on a held-out tail of the series, and keep whichever actually predicted
best — then refit that model on the full series for the real projection.
Rewritten here against this project's explicit stack (statsmodels for
Holt-Winters/SARIMA, statsforecast for Croston; no Prophet) and a plain
pandas Series input instead of raw rows.

The LLM never does this arithmetic — see app/graph/nodes.py's forecast node.
It only decides THAT a forecast is wanted and on which item/history; the
maths here is deterministic and reproducible given the same series.
"""

from __future__ import annotations

import warnings
from typing import Callable, Optional

import numpy as np
import pandas as pd

MIN_POINTS = 4                  # below this, no backtest is meaningful
MIN_NONZERO_POINTS = 2           # need at least 2 real observations to fit anything
INTERMITTENT_ZERO_FRAC = 0.35     # >= this share of zero periods = intermittent demand
DEFAULT_SEASONAL_PERIODS = 12      # monthly assumption when the index frequency is unclear

# A model function: (values, steps, seasonal_periods) -> (mean, (lo, hi) | None).
# `mean` is None when the model could not be fit at all.
ModelFn = Callable[[np.ndarray, int, int], tuple]


def _clip_nonneg(values: np.ndarray) -> np.ndarray:
    """Quantities and money cannot go negative."""
    return np.clip(np.asarray(values, dtype=float), 0.0, None)


def is_intermittent(values: np.ndarray) -> bool:
    """True when demand is sparse enough that Croston (not a trend/seasonal
    model) is the honest choice — the mate's design's own threshold."""
    if values.size == 0:
        return False
    return float(np.mean(values == 0)) >= INTERMITTENT_ZERO_FRAC


def _infer_seasonal_periods(series: pd.Series) -> int:
    """Guess the seasonal cycle length from the series' own DatetimeIndex
    frequency (series.py always builds a regular calendar grid, so this is
    normally exact, not a guess)."""
    if len(series.index) < 3:
        return DEFAULT_SEASONAL_PERIODS
    freq = pd.infer_freq(series.index)
    if not freq:
        return DEFAULT_SEASONAL_PERIODS
    freq = freq.upper()
    if freq.startswith("M"):
        return 12
    if freq.startswith("W"):
        return 52
    if freq.startswith("D"):
        return 7
    return DEFAULT_SEASONAL_PERIODS


# ======================================================================
#  Candidate models — each returns (mean_array_or_None, (lo, hi)_or_None)
# ======================================================================
def _fit_mean(values: np.ndarray, steps: int, m: int):
    if values.size == 0:
        return None, None
    return _clip_nonneg(np.full(steps, values.mean())), None


def _fit_linear(values: np.ndarray, steps: int, m: int):
    n = values.size
    if n < 2:
        return None, None
    x = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(x, values, 1)
    future = np.arange(n, n + steps, dtype=float)
    return _clip_nonneg(slope * future + intercept), None


def _fit_seasonal_naive(values: np.ndarray, steps: int, m: int):
    """Repeat the last full season — a strong, honest seasonal baseline."""
    if not m or values.size < m:
        return None, None
    last_season = values[-m:]
    return _clip_nonneg(np.array([last_season[i % m] for i in range(steps)])), None


def _estimate_d(values: np.ndarray, max_d: int = 1) -> int:
    """0 or 1: does the series need first-differencing to look stationary?
    An Augmented Dickey-Fuller test, capped at one difference — these series
    are short (a few dozen points at most), so a second difference would
    just fit noise."""
    try:
        from statsmodels.tsa.stattools import adfuller
    except Exception:
        return 1
    series = values
    for d in range(max_d + 1):
        if len(series) < 4:
            return d
        try:
            pvalue = adfuller(series, autolag="AIC")[1]
        except Exception:
            return d
        if pvalue < 0.05:
            return d
        series = np.diff(series)
    return max_d


def _search_sarima_order(values: np.ndarray, m: int, seasonal: bool):
    """Pick (p,d,q)(P,D,Q,m) for THIS series by AIC over a small grid,
    instead of one fixed guess for every series (the standard
    "test-then-search" approach, e.g. pmdarima's auto_arima)."""
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    d = _estimate_d(values)
    D = 1 if seasonal else 0
    best_aic = float("inf")
    best_order = (1, d, 1)
    best_seasonal_order = (1, D, 0, m) if seasonal else (0, 0, 0, 0)

    pq_range = range(0, 2) if seasonal else range(0, 3)
    P_range = range(0, 2) if seasonal else (0,)
    Q_range = range(0, 2) if seasonal else (0,)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for p in pq_range:
            for q in pq_range:
                if p == 0 and q == 0 and d == 0:
                    continue
                for P in P_range:
                    for Q in Q_range:
                        order = (p, d, q)
                        seasonal_order = (P, D, Q, m) if seasonal else (0, 0, 0, 0)
                        try:
                            fit = SARIMAX(
                                values, order=order, seasonal_order=seasonal_order,
                                enforce_stationarity=False, enforce_invertibility=False,
                            ).fit(disp=False)
                        except Exception:
                            continue
                        if fit.aic < best_aic:
                            best_aic, best_order, best_seasonal_order = fit.aic, order, seasonal_order
    return best_order, best_seasonal_order


def _fit_sarima(values: np.ndarray, steps: int, m: int):
    n = values.size
    if n < MIN_POINTS:
        return None, None
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
    except Exception:
        return None, None

    seasonal = bool(m and n >= 2 * m)
    try:
        order, seasonal_order = _search_sarima_order(values, m, seasonal)
    except Exception:
        order = (1, 1, 1)
        seasonal_order = (1, 1, 0, m) if seasonal else (0, 0, 0, 0)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = SARIMAX(
                values, order=order, seasonal_order=seasonal_order,
                enforce_stationarity=False, enforce_invertibility=False,
            ).fit(disp=False)
            fc = fit.get_forecast(steps)
        mean = _clip_nonneg(fc.predicted_mean)
        ci = np.asarray(fc.conf_int(alpha=0.05))
        return mean, (_clip_nonneg(ci[:, 0]), _clip_nonneg(ci[:, 1]))
    except Exception:
        return None, None


def _fit_holt_winters(values: np.ndarray, steps: int, m: int):
    """State-space ETS (statsmodels) — Holt-Winters with a real conf_int,
    unlike the classic (non-state-space) ExponentialSmoothing API."""
    n = values.size
    if n < MIN_POINTS:
        return None, None
    try:
        from statsmodels.tsa.statespace.exponential_smoothing import ExponentialSmoothing
    except Exception:
        return None, None

    seasonal = m if (m and n >= 2 * m) else None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = ExponentialSmoothing(values, trend=True, seasonal=seasonal).fit(disp=False)
            fc = fit.get_forecast(steps)
        mean = _clip_nonneg(fc.predicted_mean)
        ci = np.asarray(fc.conf_int(alpha=0.05))
        return mean, (_clip_nonneg(ci[:, 0]), _clip_nonneg(ci[:, 1]))
    except Exception:
        return None, None


def _fit_croston(values: np.ndarray, steps: int, m: int):
    """Croston's method (statsforecast) for intermittent (many-zero) demand:
    a constant future rate, with a conformal interval when there's enough
    history to hold out windows for it."""
    if np.count_nonzero(values) < MIN_NONZERO_POINTS:
        return None, None
    try:
        from statsforecast.models import CrostonOptimized
        from statsforecast.utils import ConformalIntervals
    except Exception:
        return None, None

    try:
        n_windows = 2
        enough_for_intervals = values.size >= steps * n_windows + 2
        if enough_for_intervals:
            model = CrostonOptimized(
                prediction_intervals=ConformalIntervals(h=steps, n_windows=n_windows)
            )
            res = model.forecast(y=values, h=steps, level=[95])
            mean = _clip_nonneg(res["mean"])
            lo = _clip_nonneg(res.get("lo-95", mean))
            hi = _clip_nonneg(res.get("hi-95", mean))
            return mean, (lo, hi)
        model = CrostonOptimized()
        res = model.forecast(y=values, h=steps)
        return _clip_nonneg(res["mean"]), None
    except Exception:
        return None, None


_METHOD_REGISTRY: dict[str, tuple[str, ModelFn]] = {
    "mean": ("mean baseline", _fit_mean),
    "linear": ("linear trend", _fit_linear),
    "seasonal_naive": ("seasonal naive", _fit_seasonal_naive),
    "holtwinters": ("Holt-Winters", _fit_holt_winters),
    "sarima": ("SARIMA", _fit_sarima),
    "croston": ("Croston", _fit_croston),
}


def _candidates_for(n: int, m: int, intermittent: bool) -> list[tuple[str, ModelFn]]:
    """Which models are worth backtesting for a series of this length/shape."""
    cands = [("mean baseline", _fit_mean), ("linear trend", _fit_linear)]
    if m and n >= m + 2:
        cands.append(("seasonal naive", _fit_seasonal_naive))
    if intermittent:
        cands.append(("Croston", _fit_croston))
    else:
        # Holt-Winters/SARIMA need real history to be worth the fit — about
        # 1.5 seasonal cycles minimum; they shine at 2+ cycles (the backtest
        # decides if they actually win over the simpler baselines).
        if n >= max(int(1.5 * m), 12) if m else n >= 12:
            cands.append(("Holt-Winters", _fit_holt_winters))
            cands.append(("SARIMA", _fit_sarima))
    return cands


def _mae(pred: np.ndarray, actual: np.ndarray) -> float:
    return float(np.mean(np.abs(np.asarray(pred) - np.asarray(actual))))


def _backtest(values: np.ndarray, fn: ModelFn, h: int, m: int) -> Optional[float]:
    """Hold out the last h periods, fit on the rest, score the forecast."""
    train, test = values[:-h], values[-h:]
    if train.size < 3:
        return None
    try:
        mean, _ = fn(train, h, m)
    except Exception:
        return None
    if mean is None or len(mean) < h:
        return None
    return _mae(mean[:h], test)


def _confidence_label(best_err: Optional[float], values: np.ndarray) -> str:
    if best_err is None:
        return "low"
    denom = float(np.mean(np.abs(values))) or 1.0
    rel = best_err / denom
    if rel < 0.15:
        return "high"
    if rel < 0.35:
        return "moderate"
    return "low"


def _mae_to_ci(point: np.ndarray, mae: float) -> tuple[np.ndarray, np.ndarray]:
    """Approximate a 95% interval from backtest MAE when a model has no
    native one (mean/linear/seasonal-naive, or Croston without enough
    history for conformal windows). MAE -> SD assumes ~normal residuals
    (SD = MAE / sqrt(2/pi) = MAE * 1.2533) — a standard, defensible
    conversion, not an arbitrary fudge factor."""
    sigma = mae * 1.2533
    lo = _clip_nonneg(point - 1.96 * sigma)
    hi = point + 1.96 * sigma
    return lo, hi


def forecast_series(
    series: pd.Series,
    horizon: int,
    method: str = "auto",
    seasonal_periods: int | None = None,
) -> dict:
    """Forecast `horizon` future periods of `series`.

    Always returns a dict. `ok` is False (with a plain-English `reason`) when
    there isn't enough history to forecast responsibly — callers must show
    that reason instead of a number, never guess a figure anyway.

    `method`: "auto" (default) backtests the candidate bank and keeps the
    best; or force one of "mean", "linear", "seasonal_naive", "holtwinters",
    "sarima", "croston" directly.
    """
    values = series.to_numpy(dtype=float)
    n = values.size
    non_zero = int(np.count_nonzero(values))

    if n < MIN_POINTS or non_zero < MIN_NONZERO_POINTS:
        return {
            "ok": False,
            "reason": (
                f"Only {n} period(s) of history ({non_zero} with nonzero activity) — "
                f"at least {MIN_POINTS} periods and {MIN_NONZERO_POINTS} nonzero ones "
                "are needed to forecast responsibly. Reporting current stock/usage "
                "as-is instead of projecting a number this data can't support."
            ),
            "history_points": n,
            "nonzero_points": non_zero,
        }

    m = seasonal_periods if seasonal_periods is not None else _infer_seasonal_periods(series)
    intermittent = is_intermittent(values)
    h = max(1, min(horizon, 6, n // 4))

    if method != "auto":
        if method not in _METHOD_REGISTRY:
            raise ValueError(f"Unknown forecasting method: {method!r}")
        chosen_name, chosen_fn = _METHOD_REGISTRY[method]
        err = _backtest(values, chosen_fn, h, m) if n > h + 3 else None
    else:
        candidates = _candidates_for(n, m, intermittent)
        scored = [
            (err, name, fn)
            for name, fn in candidates
            for err in [_backtest(values, fn, h, m)]
            if err is not None
        ]
        if scored:
            scored.sort(key=lambda t: t[0])
            err, chosen_name, chosen_fn = scored[0]
        else:
            err, chosen_name, chosen_fn = None, "linear trend", _fit_linear

    mean, ci = chosen_fn(values, horizon, m)
    if mean is None:  # the chosen/forced model couldn't fit the FULL series either
        mean, ci = _fit_linear(values, horizon, m)
        chosen_name = "linear trend"
    if mean is None:
        mean, ci = _fit_mean(values, horizon, m)
        chosen_name = "mean baseline"

    if ci is not None:
        lo, hi = ci
    elif err is not None:
        lo, hi = _mae_to_ci(mean, err)
    else:
        lo, hi = mean, mean  # no basis for an interval at all — degenerate but honest

    result = {
        "ok": True,
        "method": chosen_name,
        "intermittent_demand": intermittent,
        "seasonal_periods": m,
        "history_points": n,
        "nonzero_points": non_zero,
        "horizon": horizon,
        "forecast": [round(float(v), 2) for v in mean],
        "confidence_interval_low": [round(float(v), 2) for v in lo],
        "confidence_interval_high": [round(float(v), 2) for v in hi],
        "confidence": _confidence_label(err, values),
    }
    if err is not None:
        result["backtest_mae"] = round(err, 2)
    return result
