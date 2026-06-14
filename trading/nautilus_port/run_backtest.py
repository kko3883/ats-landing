#!/usr/bin/env python3
"""
Backtest — validate FourLevelStrategy against historical 1h FX bars.

Fetches 90 days of 1h bars from yfinance (same source the old daemon uses),
converts them into Nautilus Bar objects, and runs the strategy. Produces
account/positions/fills reports so you can verify signal logic and PnL
before going live.

Usage:
    python run_backtest.py                 # EUR/USD only (default)
    python run_backtest.py --all           # All three pairs
    python run_backtest.py --days 180      # 180 days of history
"""
import argparse
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.currencies import AUD, EUR, JPY, NZD, USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity

from strategy_four_level import FourLevelConfig, FourLevelStrategy

# ── Instrument definitions ─────────────────────────────────────────────────
IDEALPRO = Venue("IDEALPRO")

PAIRS = {
    "EUR/USD": {"yf_ticker": "EURUSD=X", "base": EUR, "quote": USD},
    "AUD/JPY": {"yf_ticker": "AUDJPY=X", "base": AUD, "quote": JPY},
    "NZD/JPY": {"yf_ticker": "NZDJPY=X", "base": NZD, "quote": JPY},
}


def make_instrument(name: str) -> CurrencyPair:
    """Create a Nautilus CurrencyPair instrument for the IDEALPRO venue."""
    cfg = PAIRS[name]
    symbol = Symbol(f"{name}.IDEALPRO")
    return CurrencyPair(
        instrument_id=InstrumentId(value=symbol.value, venue=IDEALPRO),
        raw_symbol=symbol,
        base_currency=cfg["base"],
        quote_currency=cfg["quote"],
        price_precision=5,
        size_precision=0,
        price_increment=Price.from_str("0.00001"),
        size_increment=Quantity.from_str("1"),
        min_quantity=Quantity.from_str("1000"),
        max_quantity=Quantity.from_str("10000000"),
        ts_event=0,
        ts_init=0,
    )


def fetch_bars(name: str, days: int = 90) -> pd.DataFrame | None:
    """Fetch 1h OHLCV bars from yfinance for one FX pair."""
    cfg = PAIRS[name]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    print(f"  Fetching {cfg['yf_ticker']} ({start.date()} -> {end.date()})...")

    df = yf.download(cfg["yf_ticker"], start=start, end=end, interval="1h", progress=False)
    if df.empty:
        print(f"    WARNING: No data for {cfg['yf_ticker']}")
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(df.columns):
        print(f"    WARNING: Missing columns: {required - set(df.columns)}")
        return None

    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
    print(f"    {len(df)} bars")
    return df


def df_to_nautilus_bars(name: str, df: pd.DataFrame) -> list[Bar]:
    """Convert a yfinance DataFrame into Nautilus Bar objects."""
    bar_type = BarType.from_str(f"{name}.IDEALPRO-1-HOUR-MID-EXTERNAL")

    bars = []
    for idx, row in df.iterrows():
        ts = dt_to_unix_nanos(idx.to_pydatetime())
        bar = Bar(
            bar_type=bar_type,
            open=Price(row["open"], precision=5),
            high=Price(row["high"], precision=5),
            low=Price(row["low"], precision=5),
            close=Price(row["close"], precision=5),
            volume=Quantity(int(row["volume"]), precision=0),
            ts_event=ts,
            ts_init=ts,
        )
        bars.append(bar)
    return bars


def main():
    parser = argparse.ArgumentParser(description="Backtest FourLevelStrategy")
    parser.add_argument("--all", action="store_true", help="Run on all three pairs")
    parser.add_argument("--days", type=int, default=90, help="Days of history (default: 90)")
    args = parser.parse_args()

    names = list(PAIRS.keys()) if args.all else ["EUR/USD"]

    print("=" * 60)
    print(f"  ATS FX Backtest - {', '.join(names)} ({args.days}d history)")
    print("=" * 60)

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id="BT-001",
            logging=LoggingConfig(log_level="ERROR"),
        )
    )
    engine.add_venue(
        venue=IDEALPRO,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(100_000, USD)],
    )

    bar_types = []
    total_bars = 0

    for name in names:
        print(f"\n-- {name} --")
        instrument = make_instrument(name)
        engine.add_instrument(instrument)
        bar_types.append(f"{name}.IDEALPRO-1-HOUR-MID-EXTERNAL")

        df = fetch_bars(name, days=args.days)
        if df is None:
            print(f"  WARNING: Skipping {name}")
            continue

        bars = df_to_nautilus_bars(name, df)
        engine.add_data(bars)
        total_bars += len(bars)

    if total_bars == 0:
        print("No bar data loaded. Check your internet connection or yfinance access.")
        return

    strategy = FourLevelStrategy(
        FourLevelConfig(
            bar_types=bar_types,
            position_sizes={
                "EUR/USD.IDEALPRO": 100_000,
                "AUD/JPY.IDEALPRO": 250_000,
                "NZD/JPY.IDEALPRO": 250_000,
            },
            atr_multipliers={
                "EUR/USD.IDEALPRO": 5.0,
                "AUD/JPY.IDEALPRO": 10.0,
                "NZD/JPY.IDEALPRO": 10.0,
            },
        )
    )
    engine.add_strategy(strategy)

    print(f"\n{'=' * 60}")
    print(f"  Running backtest ({total_bars} total bars)...")
    print(f"{'=' * 60}\n")

    engine.run()

    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)
    print(engine.trader.generate_account_report(IDEALPRO))
    print(engine.trader.generate_positions_report())
    print(engine.trader.generate_order_fills_report())

    engine.dispose()


if __name__ == "__main__":
    main()