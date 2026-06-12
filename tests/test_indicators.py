"""Indicator correctness and the leak guard (BRIEF.md sections 3 and 6.1).

The leak guard is the load-bearing test: it shuffles every bar after index T
and asserts the indicator value at T is unchanged. Any accidental use of
future data (centered window, full-sample stat) breaks it.
"""

import math

import polars as pl
import pytest

import indicators as ind


def _ramp(n: int, start: float = 100.0, step: float = 1.0) -> pl.Series:
    return pl.Series("close", [start + step * i for i in range(n)], dtype=pl.Float64)


# --- correctness --------------------------------------------------------------


def test_log_returns_basic():
    out = ind.log_returns(pl.Series([100.0, 110.0, 99.0]))
    assert out[0] is None
    assert out[1] == pytest.approx(math.log(110 / 100))
    assert out[2] == pytest.approx(math.log(99 / 110))


def test_realized_vol_constant_returns_is_zero():
    # geometric ramp => constant log returns => zero rolling std
    close = pl.Series([100.0 * (1.01 ** i) for i in range(30)])
    rv = ind.realized_vol(close, window=10)
    assert rv[-1] == pytest.approx(0.0, abs=1e-9)


def test_rolling_percentile_monotonic_increasing_is_one():
    pct = ind.rolling_percentile(_ramp(60), window=20, min_periods=20)
    # in a strictly increasing series the current value is always the max
    assert pct[-1] == pytest.approx(1.0)
    assert pct[19] == pytest.approx(1.0)
    assert pct[18] is None  # min_periods not met


def test_rolling_percentile_known_window():
    s = pl.Series([5.0, 1.0, 3.0, 2.0, 4.0])
    pct = ind.rolling_percentile(s, window=5, min_periods=5)
    # at the last row, 4 is >= {1,3,2,4} = 4 of 5 values
    assert pct[-1] == pytest.approx(4 / 5)


def test_vix_term_structure_sign():
    ts = ind.vix_term_structure(pl.Series([18.0, 22.0]), pl.Series([20.0, 20.0]))
    assert ts[0] < 1.0  # contango
    assert ts[1] > 1.0  # backwardation


def test_momentum_zscore_positive_in_uptrend():
    close = pl.Series([100.0 * (1.005 ** i) for i in range(400)])
    z = ind.momentum_zscore(close, horizons=[63, 126], z_window=126)
    assert z[-1] is not None and z[-1] > 0


def test_adx_rises_in_strong_trend():
    n = 120
    close = _ramp(n, step=1.0)
    high = close + 0.5
    low = close - 0.5
    out = ind.adx(high, low, close, window=14)
    adx_last = out.get_column("adx")[-1]
    plus_di = out.get_column("plus_di")[-1]
    minus_di = out.get_column("minus_di")[-1]
    assert adx_last > 40  # persistent uptrend => high ADX
    assert plus_di > minus_di  # direction up


# --- leak guard ---------------------------------------------------------------


def _make_panel(n: int, seed: float = 1.0) -> pl.DataFrame:
    # deterministic pseudo-random walk, no RNG (Workflow-safe and reproducible)
    closes, price = [], 100.0
    for i in range(n):
        price *= 1.0 + 0.02 * math.sin(seed * i * 0.7) + 0.001 * ((i * 37) % 11 - 5)
        closes.append(price)
    close = pl.Series("close", closes)
    return pl.DataFrame({"close": close, "high": close * 1.01, "low": close * 0.99})


@pytest.mark.parametrize(
    "fn",
    [
        lambda df: ind.realized_vol(df.get_column("close"), window=20),
        lambda df: ind.rolling_zscore(df.get_column("close"), window=30),
        lambda df: ind.rolling_percentile(df.get_column("close"), window=30, min_periods=30),
        lambda df: ind.momentum_zscore(df.get_column("close"), horizons=[21, 63], z_window=63),
        lambda df: ind.adx(df.get_column("high"), df.get_column("low"), df.get_column("close")).get_column("adx"),
    ],
)
def test_no_future_leak(fn):
    """Value at T must not change when bars after T are replaced/shuffled."""
    n, t = 300, 200
    panel = _make_panel(n)
    baseline = fn(panel)[t]

    # replace the entire tail after T with a wildly different series
    tail = _make_panel(n, seed=9.0).slice(t + 1, n)
    head = panel.slice(0, t + 1)
    perturbed = pl.concat([head, tail])
    after = fn(perturbed)[t]

    if baseline is None:
        assert after is None
    else:
        assert after == pytest.approx(baseline, rel=1e-9, abs=1e-9)
