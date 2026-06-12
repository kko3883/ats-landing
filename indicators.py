"""Pure, backward-looking indicator functions for the Thermometer and the
Phase 3 signal library.

House rule (BRIEF.md section 3): every function here uses ONLY past bars —
rolling windows, .shift(), and Wilder/EWM smoothing. No full-sample statistics,
no centered windows, no future leakage. Consequence: the value at day T is
invariant to any data after T, which tests/test_indicators.py asserts directly
by shuffling the tail and checking T is unchanged.

All functions take and return polars Series (or a small DataFrame for ADX,
which is inherently multi-column). Inputs are assumed already sorted ascending
by date.
"""

from __future__ import annotations

import polars as pl

TRADING_DAYS = 252


def log_returns(close: pl.Series) -> pl.Series:
    return (close / close.shift(1)).log()


def realized_vol(close: pl.Series, window: int) -> pl.Series:
    """Annualized rolling realized volatility from daily log returns."""
    returns = log_returns(close)
    return returns.rolling_std(window_size=window) * (TRADING_DAYS ** 0.5)


def rolling_zscore(series: pl.Series, window: int) -> pl.Series:
    """Z-score of the current value against its own trailing window."""
    mean = series.rolling_mean(window_size=window)
    std = series.rolling_std(window_size=window)
    return (series - mean) / std


def rolling_percentile(series: pl.Series, window: int, min_periods: int | None = None) -> pl.Series:
    """Fraction of the trailing `window` values (inclusive of current) that are
    <= the current value, in [0, 1]. Backward-looking by construction."""
    min_periods = min_periods or window

    def pct(window_vals: pl.Series) -> float | None:
        current = window_vals[-1]
        valid = window_vals.drop_nulls()
        if current is None or valid.len() == 0:
            return None
        return (valid <= current).sum() / valid.len()

    return series.rolling_map(pct, window_size=window, min_samples=min_periods)


def vix_term_structure(vix: pl.Series, vix3m: pl.Series) -> pl.Series:
    """VIX / VIX3M. < 1 is contango (calm); > 1 is backwardation (stress)."""
    return vix / vix3m


def momentum_zscore(close: pl.Series, horizons: list[int], z_window: int) -> pl.Series:
    """Mean across horizons of each horizon's total-return z-scored over its own
    trailing window. Sign is trend direction; magnitude is trend strength."""
    z_cols = {}
    for h in horizons:
        ret = close / close.shift(h) - 1.0
        z_cols[f"z_{h}"] = rolling_zscore(ret, z_window)
    return pl.DataFrame(z_cols).select(pl.mean_horizontal(pl.all())).to_series()


def adx(high: pl.Series, low: pl.Series, close: pl.Series, window: int = 14) -> pl.DataFrame:
    """Wilder's ADX with +DI/-DI. Returns columns: adx, plus_di, minus_di.
    ADX is trend strength (direction-agnostic); +DI vs -DI gives direction."""
    alpha = 1.0 / window
    df = pl.DataFrame({"high": high, "low": low, "close": close})
    return (
        df.with_columns(
            prev_close=pl.col("close").shift(1),
            up_move=pl.col("high") - pl.col("high").shift(1),
            down_move=pl.col("low").shift(1) - pl.col("low"),
        )
        .with_columns(
            true_range=pl.max_horizontal(
                pl.col("high") - pl.col("low"),
                (pl.col("high") - pl.col("prev_close")).abs(),
                (pl.col("low") - pl.col("prev_close")).abs(),
            ),
            plus_dm=pl.when((pl.col("up_move") > pl.col("down_move")) & (pl.col("up_move") > 0))
            .then(pl.col("up_move"))
            .otherwise(0.0),
            minus_dm=pl.when((pl.col("down_move") > pl.col("up_move")) & (pl.col("down_move") > 0))
            .then(pl.col("down_move"))
            .otherwise(0.0),
        )
        .with_columns(
            atr=pl.col("true_range").ewm_mean(alpha=alpha, adjust=False, min_samples=window),
            plus_dm_s=pl.col("plus_dm").ewm_mean(alpha=alpha, adjust=False, min_samples=window),
            minus_dm_s=pl.col("minus_dm").ewm_mean(alpha=alpha, adjust=False, min_samples=window),
        )
        .with_columns(
            plus_di=100.0 * pl.col("plus_dm_s") / pl.col("atr"),
            minus_di=100.0 * pl.col("minus_dm_s") / pl.col("atr"),
        )
        .with_columns(
            dx=pl.when((pl.col("plus_di") + pl.col("minus_di")) == 0)
            .then(None)
            .otherwise(
                100.0
                * (pl.col("plus_di") - pl.col("minus_di")).abs()
                / (pl.col("plus_di") + pl.col("minus_di"))
            )
        )
        .with_columns(
            adx=pl.col("dx").ewm_mean(alpha=alpha, adjust=False, min_samples=window)
        )
        .select("adx", "plus_di", "minus_di")
    )
