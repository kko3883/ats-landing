"""Phase 1 data layer — daily-bar ingest into a local Parquet store.

BRIEF.md section 4. Sources: FRED (credit spread, trade-weighted USD) and
yfinance (vol indices + ETF universe). Store layout: data/<source>/<ticker>.parquet.

Properties enforced here and by tests/test_ingest.py:
- All date columns are pl.Datetime("us", "UTC"), pinned to the bar's session
  calendar date at midnight UTC (tz-aware exchange timestamps keep their
  local session date so FRED and yfinance frames join on the same key).
- Incremental append only: the local store is checked before any API call
  and only rows with new dates are appended. Re-running is idempotent.
- Polars everywhere; pandas exists only at the yfinance boundary and is
  converted immediately.

Usage:
    uv run python ingest_engine.py refresh
    uv run python ingest_engine.py report
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
from typing import Callable

import polars as pl
import requests
import yaml
import yfinance as yf
from dotenv import load_dotenv

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
REQUEST_TIMEOUT_S = 30

FRED_SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Datetime("us", "UTC"),
    "value": pl.Float64,
}
YF_SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Datetime("us", "UTC"),
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
}

SUMMARY_FIELDS = ("source", "series", "status", "rows_before", "rows_added", "rows_total", "last_date")


def load_config(path: Path | str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


# --- normalization -----------------------------------------------------------


def normalize_utc(frame: pl.DataFrame, column: str = "date") -> pl.DataFrame:
    """Normalize ``column`` to pl.Datetime("us", "UTC") at the session date.

    - tz-aware datetimes: take the calendar date in their own timezone (the
      trading-session date), pinned to midnight UTC
    - naive datetimes: assumed to already be UTC wall time
    - Date / ISO-date strings: midnight UTC
    """
    dtype = frame.schema[column]
    col = pl.col(column)
    if isinstance(dtype, pl.Datetime):
        if dtype.time_zone is None:
            expr = col.dt.replace_time_zone("UTC")
        else:
            expr = col.dt.date().cast(pl.Datetime("us")).dt.replace_time_zone("UTC")
    elif dtype == pl.Date:
        expr = col.cast(pl.Datetime("us")).dt.replace_time_zone("UTC")
    elif dtype == pl.String:
        expr = col.str.to_date().cast(pl.Datetime("us")).dt.replace_time_zone("UTC")
    else:
        raise TypeError(f"cannot normalize column {column!r} of dtype {dtype} to UTC")
    return (
        frame.with_columns(expr.cast(pl.Datetime("us", "UTC")).alias(column))
        .sort(column)
    )


# --- storage -----------------------------------------------------------------


def parquet_path(data_dir: Path | str, source: str, name: str) -> Path:
    return Path(data_dir) / source / f"{name}.parquet"


def read_store(path: Path) -> pl.DataFrame | None:
    return pl.read_parquet(path) if path.exists() else None


def write_store(path: Path, frame: pl.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)


def merge_incremental(
    existing: pl.DataFrame | None, new: pl.DataFrame, key: str = "date"
) -> pl.DataFrame:
    """Append-only merge: rows whose ``key`` already exists are dropped, so
    stored history is never rewritten and re-running is idempotent."""
    new = new.unique(subset=[key], keep="last").sort(key)
    if existing is None or existing.is_empty():
        return new
    fresh = new.join(existing.select(key), on=key, how="anti")
    return pl.concat([existing, fresh], how="vertical_relaxed").sort(key)


# --- fetchers ----------------------------------------------------------------


def fetch_fred(series_id: str, api_key: str, start: dt.date) -> pl.DataFrame:
    response = requests.get(
        FRED_OBSERVATIONS_URL,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start.isoformat(),
        },
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    observations = response.json()["observations"]
    if not observations:
        return pl.DataFrame(schema=FRED_SCHEMA)
    frame = pl.DataFrame(
        {
            "date": [obs["date"] for obs in observations],
            "value": [obs["value"] for obs in observations],
        },
        schema={"date": pl.String, "value": pl.String},
    ).with_columns(
        # FRED encodes missing observations as "."
        pl.when(pl.col("value") == ".")
        .then(None)
        .otherwise(pl.col("value"))
        .cast(pl.Float64)
        .alias("value")
    )
    return normalize_utc(frame)


def fetch_yfinance(ticker: str, start: dt.date, auto_adjust: bool) -> pl.DataFrame:
    history = yf.Ticker(ticker).history(
        start=start.isoformat(), interval="1d", auto_adjust=auto_adjust, actions=False
    )
    if history.empty:
        return pl.DataFrame(schema=YF_SCHEMA)
    history.index.name = "Date"
    # pandas ends here; polars from the boundary on
    frame = pl.from_pandas(history.reset_index()).rename(
        {"Date": "date", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"},
        strict=False,
    )
    value_cols = [c for c in ("open", "high", "low", "close", "volume") if c in frame.columns]
    frame = frame.select(["date", *value_cols]).with_columns(
        pl.col(value_cols).cast(pl.Float64)
    )
    return normalize_utc(frame)


# --- refresh -----------------------------------------------------------------


def refresh_series(
    path: Path, fetch: Callable[[dt.date], pl.DataFrame], default_start: dt.date
) -> dict:
    """Fetch from the day after the last stored bar (one-and-done caching)
    and append only new rows. Returns a summary record."""
    existing = read_store(path)
    if existing is None or existing.is_empty():
        start = default_start
        rows_before = 0
    else:
        last = existing.get_column("date").max()
        start = (last + dt.timedelta(days=1)).date()
        rows_before = existing.height
    merged = merge_incremental(existing, fetch(start))
    if merged.is_empty():
        return {"status": "empty", "rows_before": 0, "rows_added": 0, "rows_total": 0, "last_date": None}
    write_store(path, merged)
    return {
        "status": "ok",
        "rows_before": rows_before,
        "rows_added": merged.height - rows_before,
        "rows_total": merged.height,
        "last_date": str(merged.get_column("date").max().date()),
    }


def refresh_all(config: dict) -> pl.DataFrame:
    """Refresh every configured series. Never raises on a single series:
    failures land in the summary's status column."""
    data_dir = Path(config["data"]["dir"])
    default_start = dt.date.fromisoformat(config["data"]["start_date"])
    fred_series = config["sources"]["fred"]["series"]
    yf_config = config["sources"]["yfinance"]
    api_key = os.environ.get("FRED_API_KEY")

    jobs: list[tuple[str, str, Callable[[dt.date], pl.DataFrame] | None]] = []
    for series_id in fred_series:
        fetch = (
            (lambda start, sid=series_id: fetch_fred(sid, api_key, start)) if api_key else None
        )
        jobs.append(("fred", series_id, fetch))
    for ticker in yf_config["tickers"]:
        jobs.append(
            (
                "yfinance",
                ticker,
                lambda start, t=ticker: fetch_yfinance(t, start, yf_config["auto_adjust"]),
            )
        )

    summaries = []
    for source, name, fetch in jobs:
        record = {"source": source, "series": name}
        if fetch is None:
            record.update(status="skipped: FRED_API_KEY not set", rows_before=0, rows_added=0, rows_total=0, last_date=None)
        else:
            try:
                record.update(refresh_series(parquet_path(data_dir, source, name), fetch, default_start))
            except Exception as exc:  # noqa: BLE001 — one bad series must not block the rest
                record.update(status=f"error: {exc}", rows_before=0, rows_added=0, rows_total=0, last_date=None)
        summaries.append({field: record[field] for field in SUMMARY_FIELDS})
    return pl.DataFrame(summaries)


# --- data-quality report -----------------------------------------------------


def quality_report(config: dict, now: dt.datetime | None = None) -> pl.DataFrame:
    """Gaps, staleness, and null counts per stored series (BRIEF.md section 4)."""
    data_dir = Path(config["data"]["dir"])
    stale_after = config["data"]["stale_after_days"]
    gap_alert = config["data"]["gap_alert_days"]
    now = now or dt.datetime.now(dt.timezone.utc)

    rows = []
    for source, names in (
        ("fred", config["sources"]["fred"]["series"]),
        ("yfinance", config["sources"]["yfinance"]["tickers"]),
    ):
        for name in names:
            record = {"source": source, "series": name}
            frame = read_store(parquet_path(data_dir, source, name))
            if frame is None or frame.is_empty():
                record.update(
                    rows=0, first_date=None, last_date=None, days_stale=None,
                    stale=True, n_gaps=0, max_gap_days=0, nulls=0,
                )
            else:
                dates = frame.get_column("date")
                gap_days = dates.diff().dt.total_days()
                days_stale = (now - dates.max()).days
                nulls = int(
                    frame.drop("date").null_count().select(pl.sum_horizontal(pl.all())).item()
                )
                record.update(
                    rows=frame.height,
                    first_date=str(dates.min().date()),
                    last_date=str(dates.max().date()),
                    days_stale=days_stale,
                    stale=days_stale > stale_after,
                    n_gaps=int((gap_days > gap_alert).sum()),
                    max_gap_days=int(gap_days.max() or 0),
                    nulls=nulls,
                )
            rows.append(record)
    return pl.DataFrame(rows)


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ATS Phase 1 data ingest")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("refresh", help="incrementally refresh all configured series")
    sub.add_parser("report", help="data-quality report for the local store")
    args = parser.parse_args(argv)

    load_dotenv()
    config = load_config()
    table = refresh_all(config) if args.command == "refresh" else quality_report(config)
    # ascii_tables: Windows consoles (cp950 et al.) choke on Unicode borders
    with pl.Config(tbl_rows=-1, tbl_cols=-1, fmt_str_lengths=80, ascii_tables=True):
        print(table)


if __name__ == "__main__":
    main()
