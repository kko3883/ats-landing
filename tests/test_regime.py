"""Thermometer tests (BRIEF.md section 5): hysteresis, the Supabase contract
mapping, and the acceptance timeline against the real local store.

The acceptance-timeline tests are skipped automatically if the Parquet store
has not been built yet (CI without data), so unit logic always runs.
"""

import datetime as dt

import polars as pl
import pytest

import regime_detector as rd
from ingest_engine import load_config, parquet_path, read_store

UTC = dt.timezone.utc


# --- hysteresis ---------------------------------------------------------------


def test_hysteresis_holds_until_n_consecutive():
    raw = [rd.BULL] * 3 + [rd.BEAR] * 5 + [rd.BULL] * 2
    # n=3: BEAR must persist 3 days before the official state flips. The first
    # 2 BEAR days hold BULL; the 3rd flips to BEAR; the trailing 2 BULLs are
    # too few (2 < 3) to flip back, so they stay BEAR.
    out = rd.apply_hysteresis(raw, n=3)
    assert out == [rd.BULL] * 5 + [rd.BEAR] * 5


def test_hysteresis_resets_on_interruption():
    # BEAR run interrupted before reaching n never flips the official state
    raw = [rd.BULL] * 2 + [rd.BEAR, rd.BEAR, rd.BULL, rd.BEAR, rd.BEAR]
    out = rd.apply_hysteresis(raw, n=3)
    assert set(out) == {rd.BULL}


def test_hysteresis_nulls_hold_state():
    raw = [rd.BULL, None, None, rd.BULL]
    assert rd.apply_hysteresis(raw, n=2) == [rd.BULL, rd.BULL, rd.BULL, rd.BULL]


def test_hysteresis_leading_nulls():
    raw = [None, None, rd.RANGE, rd.RANGE]
    assert rd.apply_hysteresis(raw, n=2) == [None, None, rd.RANGE, rd.RANGE]


def test_hysteresis_fast_shock_entry_with_n1():
    raw = [rd.RANGE, rd.SHOCK, rd.RANGE]
    assert rd.apply_hysteresis(raw, n=1) == [rd.RANGE, rd.SHOCK, rd.RANGE]


# --- Supabase contract mapping ------------------------------------------------


def test_contract_maps_all_states():
    expected = {
        rd.BULL: "risk_on",
        rd.RANGE: "choppy",
        rd.BEAR: "risk_off",
        rd.SHOCK: "risk_off",
    }
    for state, regime_name in expected.items():
        row = rd.to_regime_contract(state, vol_score=42.0)
        assert row["regime_name"] == regime_name
        assert row["state"] == state
        assert isinstance(row["activated_groups"], list) and row["activated_groups"]
        assert row["vol_score"] == 42.0


# --- acceptance timeline (real data) ------------------------------------------


def _store_ready(cfg) -> bool:
    return all(
        read_store(parquet_path(cfg["data"]["dir"], "yfinance", t)) is not None
        for t in ("SPY", "QQQ", "^VIX", "^VIX3M")
    ) and read_store(parquet_path(cfg["data"]["dir"], "fred", "BAA10Y")) is not None


@pytest.fixture(scope="module")
def timeline():
    cfg = load_config()
    if not _store_ready(cfg):
        pytest.skip("Parquet store not built — run ingest_engine.py refresh")
    return rd.build_timeline(cfg)


def test_timeline_states_are_valid(timeline):
    states = set(timeline.drop_nulls("state").get_column("state").unique().to_list())
    assert states <= set(rd.STATES)


def test_regime_score_in_bounds(timeline):
    rs = timeline.drop_nulls("regime_score").get_column("regime_score")
    assert rs.min() >= -100.0 and rs.max() <= 100.0


def test_vol_score_in_bounds(timeline):
    vs = timeline.drop_nulls("vol_score").get_column("vol_score")
    assert vs.min() >= 0.0 and vs.max() <= 100.0


def test_acceptance_checks_all_pass(timeline):
    failures = [(name, detail) for name, ok, detail in rd.acceptance_checks(timeline) if not ok]
    assert not failures, f"acceptance checks failed: {failures}"
