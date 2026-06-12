"""Phase 1 acceptance tests (BRIEF.md section 4): re-running the ingest twice
produces zero duplicate rows, and every stored frame is UTC."""

import datetime as dt

import polars as pl
import pytest

import ingest_engine as ie

UTC = dt.timezone.utc
UTC_DTYPE = pl.Datetime("us", "UTC")


def _frame(dates: list[str], values: list[float | None]) -> pl.DataFrame:
    return ie.normalize_utc(pl.DataFrame({"date": dates, "value": values}))


# --- normalize_utc ------------------------------------------------------------


def test_normalize_utc_from_strings():
    frame = _frame(["2024-01-03", "2024-01-02"], [2.0, 1.0])
    assert frame.schema["date"] == UTC_DTYPE
    assert frame.get_column("date").to_list() == [
        dt.datetime(2024, 1, 2, tzinfo=UTC),
        dt.datetime(2024, 1, 3, tzinfo=UTC),
    ]


def test_normalize_utc_from_date_and_naive_datetime():
    for column in (
        pl.Series("date", [dt.date(2024, 1, 2)]),
        pl.Series("date", [dt.datetime(2024, 1, 2)]),
    ):
        frame = ie.normalize_utc(pl.DataFrame(column))
        assert frame.schema["date"] == UTC_DTYPE
        assert frame.item(0, "date") == dt.datetime(2024, 1, 2, tzinfo=UTC)


def test_normalize_utc_keeps_exchange_session_date():
    # Midnight Eastern is 05:00 UTC; the session date must stay 2024-01-02,
    # not shift to the UTC-converted timestamp's date/time.
    frame = pl.DataFrame(
        pl.Series("date", [dt.datetime(2024, 1, 2)]).dt.replace_time_zone("America/New_York")
    )
    out = ie.normalize_utc(frame)
    assert out.item(0, "date") == dt.datetime(2024, 1, 2, tzinfo=UTC)


def test_normalize_utc_rejects_unknown_dtype():
    with pytest.raises(TypeError):
        ie.normalize_utc(pl.DataFrame({"date": [1, 2]}))


# --- merge_incremental ---------------------------------------------------------


def test_merge_incremental_appends_only_new_rows():
    existing = _frame(["2024-01-02", "2024-01-03"], [1.0, 2.0])
    new = _frame(["2024-01-03", "2024-01-04"], [99.0, 3.0])
    merged = ie.merge_incremental(existing, new)
    assert merged.height == 3
    assert merged.get_column("date").n_unique() == 3
    # append-only: the stored 2024-01-03 row wins over the refetched one
    assert merged.filter(pl.col("value") == 99.0).is_empty()


def test_merge_incremental_is_idempotent():
    data = _frame(["2024-01-02", "2024-01-03"], [1.0, 2.0])
    once = ie.merge_incremental(None, data)
    twice = ie.merge_incremental(once, data)
    assert twice.height == once.height == 2


# --- fetchers ------------------------------------------------------------------


class _StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_fred_parses_payload(monkeypatch):
    payload = {
        "observations": [
            {"date": "2024-01-03", "value": "3.55"},
            {"date": "2024-01-02", "value": "."},  # FRED missing-value marker
        ]
    }
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _StubResponse(payload)

    monkeypatch.setattr(ie.requests, "get", fake_get)
    frame = ie.fetch_fred("BAMLH0A0HYM2", "key", dt.date(2024, 1, 1))
    assert captured["params"]["observation_start"] == "2024-01-01"
    assert dict(frame.schema) == ie.FRED_SCHEMA
    assert frame.get_column("value").to_list() == [None, 3.55]  # sorted by date


def test_fetch_fred_empty_payload(monkeypatch):
    monkeypatch.setattr(ie.requests, "get", lambda *a, **k: _StubResponse({"observations": []}))
    frame = ie.fetch_fred("BAMLH0A0HYM2", "key", dt.date(2024, 1, 1))
    assert frame.is_empty()
    assert dict(frame.schema) == ie.FRED_SCHEMA


def test_fetch_yfinance_converts_at_boundary(monkeypatch):
    pd = pytest.importorskip("pandas")
    index = pd.date_range("2024-01-02", periods=2, freq="D", tz="America/New_York")
    history = pd.DataFrame(
        {
            "Open": [1.0, 2.0],
            "High": [1.5, 2.5],
            "Low": [0.5, 1.5],
            "Close": [1.2, 2.2],
            "Volume": [100, 200],
        },
        index=index,
    )

    class StubTicker:
        def __init__(self, ticker):
            pass

        def history(self, **kwargs):
            return history

    monkeypatch.setattr(ie.yf, "Ticker", StubTicker)
    frame = ie.fetch_yfinance("SPY", dt.date(2024, 1, 1), auto_adjust=True)
    assert dict(frame.schema) == ie.YF_SCHEMA
    assert frame.get_column("date").to_list() == [
        dt.datetime(2024, 1, 2, tzinfo=UTC),
        dt.datetime(2024, 1, 3, tzinfo=UTC),
    ]


def test_fetch_yfinance_empty_history(monkeypatch):
    pd = pytest.importorskip("pandas")

    class StubTicker:
        def __init__(self, ticker):
            pass

        def history(self, **kwargs):
            return pd.DataFrame()

    monkeypatch.setattr(ie.yf, "Ticker", StubTicker)
    frame = ie.fetch_yfinance("SPY", dt.date(2024, 1, 1), auto_adjust=True)
    assert frame.is_empty()
    assert dict(frame.schema) == ie.YF_SCHEMA


# --- refresh (the acceptance test) ----------------------------------------------


def test_refresh_series_rerun_adds_zero_duplicates(tmp_path):
    data = _frame(
        ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"],
        [1.0, 2.0, 3.0, 4.0, 5.0],
    )
    starts = []

    def fetch(start):
        starts.append(start)
        return data

    path = ie.parquet_path(tmp_path, "fred", "TEST")
    first = ie.refresh_series(path, fetch, dt.date(2024, 1, 1))
    second = ie.refresh_series(path, fetch, dt.date(2024, 1, 1))

    assert first["rows_added"] == 5
    assert second["rows_added"] == 0
    stored = pl.read_parquet(path)
    assert stored.height == 5
    assert stored.get_column("date").n_unique() == 5
    assert stored.schema["date"] == UTC_DTYPE
    # incremental: the second run asked only for bars after the last stored one
    assert starts == [dt.date(2024, 1, 1), dt.date(2024, 1, 9)]


def _config(tmp_path, fred_series, yf_tickers):
    return {
        "data": {
            "dir": str(tmp_path),
            "start_date": "2024-01-01",
            "stale_after_days": 5,
            "gap_alert_days": 4,
        },
        "sources": {
            "fred": {"series": fred_series},
            "yfinance": {"auto_adjust": True, "tickers": yf_tickers},
        },
    }


def test_refresh_all_skips_fred_without_key_and_isolates_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    canned = ie.normalize_utc(
        pl.DataFrame(
            {"date": ["2024-01-02"], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [0.0]}
        )
    )

    def fake_yf(ticker, start, auto_adjust):
        if ticker == "BAD":
            raise RuntimeError("boom")
        return canned

    monkeypatch.setattr(ie, "fetch_yfinance", fake_yf)
    summary = ie.refresh_all(_config(tmp_path, ["HYSPREAD"], ["SPY", "BAD"]))
    by_series = {row["series"]: row for row in summary.to_dicts()}
    assert by_series["HYSPREAD"]["status"].startswith("skipped")
    assert by_series["SPY"]["status"] == "ok"
    assert by_series["BAD"]["status"] == "error: boom"
    assert (tmp_path / "yfinance" / "SPY.parquet").exists()
    assert not (tmp_path / "yfinance" / "BAD.parquet").exists()


# --- quality report --------------------------------------------------------------


def test_quality_report_gaps_nulls_staleness(tmp_path):
    frame = _frame(["2024-01-02", "2024-01-03", "2024-01-10"], [1.0, None, 3.0])
    ie.write_store(ie.parquet_path(tmp_path, "fred", "TEST"), frame)
    report = ie.quality_report(
        _config(tmp_path, ["TEST"], ["MISSING"]),
        now=dt.datetime(2024, 1, 12, tzinfo=UTC),
    )
    stored = report.row(0, named=True)
    assert stored["rows"] == 3
    assert stored["n_gaps"] == 1  # 01-03 -> 01-10 is a 7-day jump
    assert stored["max_gap_days"] == 7
    assert stored["nulls"] == 1
    assert stored["days_stale"] == 2
    assert stored["stale"] is False

    missing = report.row(1, named=True)
    assert missing["series"] == "MISSING"
    assert missing["rows"] == 0
    assert missing["stale"] is True
