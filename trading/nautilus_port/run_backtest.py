#!/usr/bin/env python3
"""
Backtest -- validate FourLevelStrategy (v2 sliding-1H) against historical FX bars.

Fetches bars from IB Gateway (default) or yfinance (fallback), converts them
into Nautilus Bar objects, and runs the strategy. Supports three test variants
for A/B comparison per the implementation plan (section 8).

Usage:
    python run_backtest.py                          # full: 15m + fast trigger (default, IB)
    python run_backtest.py --data-source yfinance   # yfinance fallback
    python run_backtest.py --all                    # All three pairs
    python run_backtest.py --days 90                # 90 days of history
    python run_backtest.py --variant baseline       # 1h bars, no sliding, no fast trigger
    python run_backtest.py --variant sliding        # 15m sliding cadence, no fast trigger
"""
import argparse
import json
import os
import time
import threading
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

# ---- Instrument definitions -------------------------------------------------
IDEALPRO = Venue("IDEALPRO")

PAIRS = {
    "EUR/USD": {
        "yf_ticker": "EURUSD=X",
        "ib_base": "EUR",
        "ib_quote": "USD",
        "base": EUR,
        "quote": USD,
    },
    "AUD/JPY": {
        "yf_ticker": "AUDJPY=X",
        "ib_base": "AUD",
        "ib_quote": "JPY",
        "base": AUD,
        "quote": JPY,
    },
    "NZD/JPY": {
        "yf_ticker": "NZDJPY=X",
        "ib_base": "NZD",
        "ib_quote": "JPY",
        "base": NZD,
        "quote": JPY,
    },
}

# IB bar-size mapping: script interval -> IB barSizeSetting string
_IB_BAR_SIZE = {"1h": "1 hour", "15m": "15 mins"}

# Default connection for backtest from the Mac (Tailscale -> NAS).
IB_BACKTEST_HOST = os.environ.get("IB_BACKTEST_HOST", "kko-nas.tail9a4917.ts.net")
IB_BACKTEST_PORT = int(os.environ.get("IB_BACKTEST_PORT", "4004"))
IB_BACKTEST_CLIENT_ID = int(os.environ.get("IB_BACKTEST_CLIENT_ID", "987"))
IB_TIMEOUT_SECS = 30


def make_instrument(name: str) -> CurrencyPair:
    """Create a Nautilus CurrencyPair instrument for the IDEALPRO venue."""
    cfg = PAIRS[name]
    iid = InstrumentId.from_str(f"{name}.IDEALPRO")
    return CurrencyPair(
        instrument_id=iid,
        raw_symbol=Symbol(f"{name}.IDEALPRO"),
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


# ---- data source: yfinance -------------------------------------------------

def fetch_bars_yfinance(
    name: str, days: int = 90, interval: str = "15m",
) -> pd.DataFrame | None:
    """Fetch OHLCV bars from yfinance for one FX pair."""
    cfg = PAIRS[name]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    print(f"  Fetching yfinance:{cfg['yf_ticker']} "
          f"({start.date()} -> {end.date()}, {interval})...")

    df = yf.download(cfg["yf_ticker"], start=start, end=end,
                     interval=interval, progress=False)
    if df.empty:
        print(f"    WARNING: No data for {cfg['yf_ticker']}")
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(df.columns):
        print(f"    WARNING: Missing columns: {required - set(df.columns)}")
        return None

    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    print(f"    {len(df)} bars")
    return df


# ---- data source: IB Gateway (ibapi 9.81.1 + 10.x compatible) ----------------

# Benign gateway info codes -- do not treat as errors.
_BENIGN_CODES = frozenset({2104, 2106, 2158, 2107, 2119})


class _IBBarApp(EWrapper, EClient):
    """Minimal ibapi app that fetches historical bars and returns a DataFrame.

    Inherits directly from EWrapper+EClient per canonical ibapi pattern.
    Compatible with ibapi 9.81.1 (PyPI) and 10.x (official).
    """

    def __init__(self):
        from ibapi.client import EClient
        from ibapi.wrapper import EWrapper

        EClient.__init__(self, self)  # CRITICAL: self as wrapper

        self._done = threading.Event()       # set when historicalDataEnd fires
        self._connected = threading.Event()  # set when nextValidId fires
        self._bars: list[dict] = []
        self._req_id = 1
        self._error_msg: str | None = None
        self._last_error_code: int | None = None

    # ---- EWrapper callbacks (compatible with ibapi 9.81.1 AND 10.x) ----

    def error(self, *args):
        # ibapi 9.81.1: (self, reqId, errorCode, errorString)
        # ibapi 10.x:   (self, reqId, errorTime, errorCode, errorString,
        #                advancedOrderRejectJson)
        code = None
        msg = ""
        if len(args) >= 4:
            code = args[2]
            msg = args[3]
        elif len(args) >= 3:
            code = args[1]
            msg = args[2]
        elif len(args) >= 2:
            code = args[1]
            msg = str(args[1]) if args[1] else ""
        else:
            msg = str(args)

        # Suppress benign market-data-farm-is-connected chatter.
        if code is not None and int(code) in _BENIGN_CODES:
            return

        if msg and not self._error_msg:
            self._error_msg = msg
            self._last_error_code = int(code) if code is not None else None

    def nextValidId(self, orderId: int):
        self._connected.set()

    def historicalData(self, reqId: int, bar):
        self._bars.append({
            "date": bar.date,  # epoch seconds string from IB
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume if hasattr(bar, "volume") else 0,
        })

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        self._done.set()

    # ---- connection helpers ----

    def connect_and_wait(self, host: str, port: int, client_id: int,
                         timeout: float):
        print(f"    Connecting to IB Gateway {host}:{port} "
              f"clientId={client_id}...")
        super().connect(host, port, client_id)

        # Wait for connection readiness:
        # - ibapi 10.x: serverVersion() populated after connect.
        # - ibapi 9.81.1: nextValidId fires signalling readiness.
        t0 = time.monotonic()
        while True:
            if self._connected.is_set():
                break
            # Also check serverVersion as a secondary readiness signal.
            sv = None
            try:
                sv = self.serverVersion()
            except Exception:
                pass
            if sv is not None and sv > 0:
                self._connected.set()
                break
            if time.monotonic() - t0 > timeout:
                self.disconnect()
                raise TimeoutError(
                    f"Timed out waiting for connection after {timeout:.0f}s. "
                    f"Host={host}:{port} -- is the gateway running and is port "
                    f"{port} published in docker-compose? "
                    f"(nc -zv {host} {port} should succeed before retrying)"
                )
            time.sleep(0.1)

    def request_bars(self, name: str, days: int, interval: str) -> pd.DataFrame:
        from ibapi.contract import Contract

        cfg = PAIRS[name]
        c = Contract()
        c.symbol = cfg["ib_base"]
        c.secType = "CASH"
        c.currency = cfg["ib_quote"]
        c.exchange = "IDEALPRO"

        bar_size = _IB_BAR_SIZE.get(interval, "15 mins")
        duration = f"{days} D"
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)

        print(f"    Requesting IB:{cfg['ib_base']}.{cfg['ib_quote']} "
              f"({start.date()} -> {end.date()}, {bar_size}, {duration})...")

        # All-positional call works across ibapi 9.81.1 and 10.x.
        self.reqHistoricalData(
            self._req_id,
            c,
            "",              # endDateTime -- empty = now
            duration,        # durationStr
            bar_size,        # barSizeSetting
            "MIDPOINT",      # whatToShow
            0,               # useRTH
            2,               # formatDate = epoch seconds
            False,           # keepUpToDate (bool in both 9.81.1 and 10.x)
            [],              # chartOptions
        )

        # Run the client loop on a daemon thread. The callbacks populate
        # self._bars and set self._done when complete.
        def _loop():
            try:
                self.run()
            except Exception as e:
                if not self._done.is_set():
                    self._error_msg = self._error_msg or str(e)
                self._done.set()

        thread = threading.Thread(target=_loop, daemon=True)
        thread.start()

        # Wait for historicalDataEnd or timeout.
        if not self._done.wait(timeout=IB_TIMEOUT_SECS):
            raise TimeoutError(
                f"IB historical data request timed out after "
                f"{IB_TIMEOUT_SECS}s for {name}"
            )

        if not self._bars:
            err = self._error_msg or "no bars returned"
            print(f"    WARNING: IB returned no bars for {name}: {err}")
            return None

        df = pd.DataFrame(self._bars)
        # Convert epoch seconds to UTC datetime index.
        df["_ts"] = pd.to_datetime(
            pd.to_numeric(df["date"], errors="coerce"), unit="s", utc=True,
        )
        df = df.set_index("_ts")
        df.index.name = None
        df = df.drop(columns=["date"])
        # Ensure ascending, deduplicated.
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="first")]
        # Column names match what the downstream conversion expects.
        df = df.rename(columns={
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume",
        })
        print(f"    {len(df)} bars  ({df.index[0]} -> {df.index[-1]})")
        return df


def fetch_bars_ib(
    name: str,
    days: int = 90,
    interval: str = "15m",
    host: str = IB_BACKTEST_HOST,
    port: int = IB_BACKTEST_PORT,
    client_id: int = IB_BACKTEST_CLIENT_ID,
) -> pd.DataFrame | None:
    """Fetch OHLCV bars from IB Gateway for one FX pair. Returns a DataFrame
    identical in shape to what fetch_bars_yfinance produces."""
    app = _IBBarApp()
    try:
        app.connect_and_wait(host, port, client_id, timeout=20)
        return app.request_bars(name, days, interval)
    except Exception as e:
        print(f"    ERROR fetching from IB {host}:{port}: {e}")
        print(f"    Verify: (1) gateway is running, (2) port {port} is "
              f"published in docker-compose, "
              f"(3) 'nc -zv {host} {port}' succeeds")
        return None
    finally:
        app.disconnect()


def fetch_bars(
    name: str,
    days: int = 90,
    interval: str = "15m",
    source: str = "ib",
) -> pd.DataFrame | None:
    """Dispatch to the configured data source."""
    if source == "ib":
        return fetch_bars_ib(name, days, interval)
    else:
        return fetch_bars_yfinance(name, days, interval)


# ---- Nautilus Bar conversion ------------------------------------------------

def df_to_nautilus_bars(name: str, df: pd.DataFrame, bar_type_str: str) -> list[Bar]:
    """Convert a DataFrame into Nautilus Bar objects."""
    bar_type = BarType.from_str(bar_type_str)

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


# ---- JSONL dump helpers -----------------------------------------------------

def _df_to_jsonl(df: pd.DataFrame, path: str, label: str) -> int:
    """Serialize a DataFrame to JSONL, one JSON object per row."""
    df = df.reset_index(drop=True)
    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for _, row in df.iterrows():
            record = {}
            for col in df.columns:
                val = row[col]
                record[col] = _serialize_value(val)
            f.write(json.dumps(record, ensure_ascii=True) + "\n")
            count += 1
    print(f"DUMPED {label} rows={count} path={path}")
    return count


def _serialize_value(val):
    """Convert a single cell value to a JSON-safe Python type."""
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, (pd.Timestamp, datetime)):
        return val.isoformat()
    if isinstance(val, (pd.Period,)):
        return str(val)
    if hasattr(val, "item"):  # numpy scalar
        val = val.item()
    if isinstance(val, float):
        if val != val:
            return None
        return val
    if isinstance(val, (int, str, bool, type(None))):
        return val
    return str(val)


# ---- main -------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Backtest FourLevelStrategy")
    parser.add_argument("--all", action="store_true",
                        help="Run on all three pairs")
    parser.add_argument("--days", type=int, default=90,
                        help="Days of history (default: 90)")
    parser.add_argument(
        "--variant", choices=["baseline", "sliding", "full"], default="full",
        help="Test variant: baseline (1h, no sliding), "
             "sliding (15m sliding, no fast trigger), "
             "full (15m sliding + fast trigger, default)",
    )
    parser.add_argument(
        "--data-source", choices=["ib", "yfinance"], default="ib",
        help="Bar data source: ib (IB Gateway, default) or yfinance (fallback)",
    )
    parser.add_argument(
        "--no-period-scale", action="store_true",
        help="Disable hourly-semantics period scaling (reproduces the v2 "
             "compressed-timescale behaviour, for A/B comparison only)",
    )
    parser.add_argument("--dump-trades", metavar="PATH",
                        help="Write order fills report to JSONL file")
    parser.add_argument("--dump-positions", metavar="PATH",
                        help="Write positions report to JSONL file")
    args = parser.parse_args()

    names = list(PAIRS.keys()) if args.all else ["EUR/USD"]
    variant = args.variant
    source = args.data_source

    use_1h = (variant == "baseline")
    interval = "1h" if use_1h else "15m"
    bar_type_fmt = "{}.IDEALPRO-{}-MID-EXTERNAL"
    bar_type_spec = "1-HOUR" if use_1h else "15-MINUTE"
    sliding_window = 1 if use_1h else 4
    fast_trigger = (variant == "full")

    print("=" * 60)
    print(f"  ATS FX Backtest - {', '.join(names)} "
          f"({args.days}d, {variant}, source={source})")
    print(f"  interval={interval}  sliding_window={sliding_window}  "
          f"fast_trigger={fast_trigger}  "
          f"period_scale={'OFF (v2 compressed)' if args.no_period_scale else 'hourly'}")
    if source == "ib":
        print(f"  IB Gateway: {IB_BACKTEST_HOST}:{IB_BACKTEST_PORT}  "
              f"clientId={IB_BACKTEST_CLIENT_ID}")
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

        bt_str = bar_type_fmt.format(name, bar_type_spec)
        bar_types.append(bt_str)

        df = fetch_bars(name, days=args.days, interval=interval, source=source)
        if df is None:
            print(f"  WARNING: Skipping {name}")
            continue

        bars = df_to_nautilus_bars(name, df, bt_str)
        engine.add_data(bars)
        total_bars += len(bars)

    if total_bars == 0:
        print("No bar data loaded. Check your internet connection or "
              "IB Gateway / yfinance access.")
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
            sliding_window_bars=sliding_window,
            fast_trigger_enabled=fast_trigger,
            scale_periods_to_hourly=not args.no_period_scale,
        )
    )
    engine.add_strategy(strategy)

    print(f"\n{'=' * 60}")
    print(f"  Running backtest ({total_bars} total bars)...")
    print(f"{'=' * 60}\n")

    engine.run()

    fills_df = engine.trader.generate_order_fills_report()
    positions_df = engine.trader.generate_positions_report()

    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)
    print(engine.trader.generate_account_report(IDEALPRO))
    print(positions_df)
    print(fills_df)

    if args.dump_trades:
        _df_to_jsonl(fills_df, args.dump_trades, "fills")
    if args.dump_positions:
        _df_to_jsonl(positions_df, args.dump_positions, "positions")

    engine.dispose()


if __name__ == "__main__":
    main()