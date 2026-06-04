"""
Data source layer: wraps yfinance with local caching.

Fetches both stock price data and macro series (VIX, DXY) in parallel batches.
Cache TTL is 24 hours for all data.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

from .longbridge_data import fetch_longbridge_data

from .config import MACRO_SYMBOLS, MACRO_LABELS

CACHE_DIR = Path.home() / ".hermes" / "trading" / ".cache"
CACHE_TTL_HOURS = 24


def _cache_path(market_or_kind: str, kind: str = "prices") -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{market_or_kind}_{kind}.parquet"


def _meta_path(market_or_kind: str, kind: str = "prices") -> Path:
    return _cache_path(market_or_kind, kind).with_suffix(".meta.json")


def _cache_valid(
    meta_path: Path, ttl_hours: int = CACHE_TTL_HOURS
) -> bool:
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text())
        cached_at = datetime.fromisoformat(meta["cached_at"])
        age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
        return age_hours < ttl_hours
    except (KeyError, ValueError, json.JSONDecodeError):
        return False


def _save_cache(df: pd.DataFrame, market: str, kind: str):
    path = _cache_path(market, kind)
    meta = _meta_path(market, kind)
    df.to_parquet(path, index=True)
    meta.write_text(
        json.dumps(
            {
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "market": market,
                "kind": kind,
                "rows": len(df),
                "columns": list(df.columns),
            }
        )
    )


def _load_cache(market: str, kind: str) -> Optional[pd.DataFrame]:
    path = _cache_path(market, kind)
    meta = _meta_path(market, kind)
    if not _cache_valid(meta) or not path.exists():
        return None
    return pd.read_parquet(path)


# ── Public: Fetch Stock Prices ──────────────────────────────────────────────


def fetch_price_data(
    symbols: list[str],
    market: str,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Batch-download OHLCV for stock symbols + macro series together.

    Uses Longbridge for HK stocks, yfinance for everything else (US stocks + macro).
    Returns a single MultiIndex DataFrame with columns (Ticker, Price).
    """
    kind = "prices"

    if not force_refresh:
        cached = _load_cache(market, kind)
        if cached is not None:
            return cached

    t0 = time.time()

    if market == "hk":
        # ── HK market: Longbridge for stocks, yfinance for macro ──
        stock_data = fetch_longbridge_data(
            symbols,
            start="2025-12-01",
        )

        # Fetch macro (VIX, DXY) via yfinance separately
        try:
            macro = yf.download(
                tickers=MACRO_SYMBOLS,
                period="6mo",
                interval="1d",
                auto_adjust=True,
                threads=True,
                timeout=30,
            )
        except Exception:
            macro = pd.DataFrame()

        if not macro.empty and not stock_data.empty:
            # Both stock data and macro are in (Price, Ticker) format now
            # Align date ranges: keep dates in both
            common_dates = stock_data.index.intersection(macro.index)
            stock_data = stock_data.loc[common_dates]
            macro = macro.loc[common_dates]

            # Merge: stock data + macro (stacked horizontally)
            data = pd.concat([stock_data, macro], axis=1)
        elif not stock_data.empty:
            data = stock_data
        else:
            data = macro if not macro.empty else pd.DataFrame()

    else:
        # ── US market: try Longbridge first, fall back to yfinance ──
        print(f"  Fetching {market.upper()} data ({len(symbols)} tickers)...")
        stock_data = fetch_longbridge_data(
            symbols,
            suffix=".US",
            start="2025-12-01",
            label=f"{len(symbols)} US stocks",
        )

        if not stock_data.empty:
            # Fetch macro (VIX, DXY) via yfinance separately
            try:
                macro = yf.download(
                    tickers=MACRO_SYMBOLS,
                    period="6mo",
                    interval="1d",
                    auto_adjust=True,
                    threads=True,
                    timeout=30,
                )
            except Exception:
                macro = pd.DataFrame()

            if not macro.empty:
                common_dates = stock_data.index.intersection(macro.index)
                stock_data = stock_data.loc[common_dates]
                macro = macro.loc[common_dates]
                data = pd.concat([stock_data, macro], axis=1)
            else:
                data = stock_data
        else:
            # Fallback: yfinance for everything
            print(f"  Longbridge failed, falling back to yfinance...")
            all_tickers = symbols + MACRO_SYMBOLS
            data = yf.download(
                tickers=all_tickers,
                period="6mo",
                interval="1d",
                auto_adjust=True,
                threads=True,
                timeout=30,
            )

    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.1f}s")

    if not data.empty:
        _save_cache(data, market, kind)
    return data


# ── Public: Extract Macro Series ────────────────────────────────────────────


def extract_macro_series(
    prices: pd.DataFrame,
) -> dict[str, pd.Series]:
    """
    Extract VIX and DXY close prices from the combined price DataFrame.

    Returns {'^VIX': Series, 'DX-Y.NYB': Series}.
    """
    if prices.empty:
        return {}

    if not isinstance(prices.columns, pd.MultiIndex):
        # Cannot determine ticker structure — look for VIX/DXY in column names
        result = {}
        for sym in MACRO_SYMBOLS:
            col_label = MACRO_LABELS.get(sym, sym)
            if sym in prices.columns:
                result[col_label] = prices[sym]
        return result

    # MultiIndex: level_names = ['Ticker', 'Price'] or ['Price', 'Ticker']
    level_names = prices.columns.names
    if level_names[0] in ("Price", "price"):
        ticker_level, price_level = 1, 0
    elif level_names[1] in ("Price", "price"):
        ticker_level, price_level = 0, 1
    else:
        return {}

    result = {}
    for sym in MACRO_SYMBOLS:
        try:
            close = prices.xs("Close", axis=1, level=price_level)
            if sym in close.columns:
                result[MACRO_LABELS.get(sym, sym)] = close[sym].dropna()
        except KeyError:
            continue

    return result


# ── Public: Invalidate Cache ────────────────────────────────────────────────


def invalidate_cache(market: Optional[str] = None):
    """Clear cache for a market, or all markets if None."""
    if market:
        for p in CACHE_DIR.glob(f"{market}_*.parquet"):
            p.unlink(missing_ok=True)
        for p in CACHE_DIR.glob(f"{market}_*.meta.json"):
            p.unlink(missing_ok=True)
    else:
        import shutil

        if CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR)
            CACHE_DIR.mkdir(parents=True)
