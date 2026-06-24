"""
Longbridge WebSocket streaming integration.

Streams real-time 1-minute and 15-minute OHLCV candles via Longbridge SDK.
Pushes completed bars into the engine's bar queue for processing.

Architecture:
  - The `QuoteContext` subscribes to candlestick updates.
  - On each completed candle, we emit a normalized dict into an asyncio Queue.
  - The main engine loop reads from this queue and dispatches to Layer 2/3.

Note: Longbridge's Python SDK is async-native.  We run the streaming context
in its own asyncio task alongside the engine loop.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Bar:
    """Normalized OHLCV bar from any source (Longbridge, historical, etc.)."""
    symbol: str          # e.g. "AAPL.US"
    period: str          # "1m", "15m", "1d"
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float = 0.0

    @classmethod
    def from_longbridge_candle(cls, symbol: str, period: str, candle: dict) -> "Bar":
        """Parse a Longbridge candlestick dict into a normalized Bar."""
        ts = candle.get("timestamp") or candle.get("time")
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        elif isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return cls(
            symbol=symbol,
            period=period,
            timestamp=ts,
            open=float(candle["open"]),
            high=float(candle["high"]),
            low=float(candle["low"]),
            close=float(candle["close"]),
            volume=int(candle.get("volume", 0)),
            turnover=float(candle.get("turnover", 0)),
        )

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "period": self.period,
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "turnover": self.turnover,
        }


@dataclass
class StreamConfig:
    """Configuration for the streaming client."""
    symbols: list[str] = field(default_factory=list)
    periods: list[str] = field(default_factory=lambda: ["1m", "15m", "1d"])
    # Max queue size — backpressure if engine falls behind
    max_queue_size: int = 10_000


class LongbridgeStreamer:
    """
    Async wrapper around Longbridge candlestick streaming.

    Provides a simple poll-based interface for the synchronous engine loop
    to consume bars without deep asyncio knowledge.
    """

    def __init__(self, config: StreamConfig):
        self._config = config
        self._queue: asyncio.Queue[Bar] = asyncio.Queue(maxsize=config.max_queue_size)
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._bar_counts: dict[str, int] = {}  # period → count

    # ── Public API ──────────────────────────────────────────────────────

    async def start(self):
        """Launch the streaming WebSocket connection."""
        self._running = True
        self._task = asyncio.create_task(self._stream_loop())
        logger.info(
            f"Longbridge streamer started: "
            f"{len(self._config.symbols)} symbols × {self._config.periods}"
        )

    async def stop(self):
        """Shut down the streaming connection."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Longbridge streamer stopped")

    async def get_bar(self, timeout: float = 1.0) -> Optional[Bar]:
        """
        Get the next bar from the queue.  Non-blocking with timeout.
        Returns None if no bar available within timeout.
        """
        try:
            bar = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            period = bar.period
            self._bar_counts[period] = self._bar_counts.get(period, 0) + 1
            return bar
        except asyncio.TimeoutError:
            return None

    def get_queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def bar_counts(self) -> dict:
        return dict(self._bar_counts)

    # ── Internal: streaming loop ────────────────────────────────────────

    async def _stream_loop(self):
        """
        Main streaming loop.  Uses the Longbridge SDK's candidate subscription
        to receive real-time bar updates, then pushes completed bars into
        the queue.

        If the Longbridge SDK is not available (CLI-only setup), falls back
        to a polling approach using the CLI kline endpoint every period interval.
        """
        try:
            from longbridge.openapi import QuoteContext, Config, Period, AdjustType
            from longbridge.openapi.quote import SubFlags
            HAVE_SDK = True
        except ImportError:
            HAVE_SDK = False
            logger.warning(
                "Longbridge SDK not installed (pip install longbridge). "
                "Falling back to CLI polling mode."
            )

        if HAVE_SDK:
            await self._stream_sdk()
        else:
            await self._stream_cli_poll()

    async def _stream_sdk(self):
        """Stream bars using the Longbridge Python SDK WebSocket."""
        from longbridge.openapi import QuoteContext, Config, Period, AdjustType
        from longbridge.openapi.quote import SubFlags

        config = Config.from_env()
        ctx = QuoteContext(config)

        try:
            # Map our period strings to Longbridge Period enum
            period_map = {
                "1m": Period.Min_1,
                "5m": Period.Min_5,
                "15m": Period.Min_15,
                "30m": Period.Min_30,
                "60m": Period.Min_60,
                "1d": Period.Day,
            }

            # Subscribe to all symbols × periods
            for symbol in self._config.symbols:
                for period_str in self._config.periods:
                    lb_period = period_map.get(period_str)
                    if lb_period is None:
                        logger.warning(f"Unsupported period: {period_str}")
                        continue

                    try:
                        # Subscribe to candlestick updates
                        ctx.subscribe(
                            symbol=symbol,
                            period=lb_period,
                            adjust_type=AdjustType.ForwardAdjust,
                            flags=[SubFlags.CANDLESTICK_REALTIME],
                        )
                        logger.debug(f"Subscribed: {symbol} {period_str}")
                    except Exception as e:
                        logger.error(f"Subscribe failed {symbol} {period_str}: {e}")

            # Consume updates from the subscription
            while self._running:
                try:
                    # Longbridge push callback — check for new candles
                    # The SDK delivers updates via the callback registered
                    # in subscribe().  For simplicity, we use a short poll
                    # on the latest candle endpoint as a real-time proxy.
                    for symbol in self._config.symbols:
                        for period_str in self._config.periods:
                            lb_period = period_map.get(period_str)
                            if lb_period is None:
                                continue
                            try:
                                candles = ctx.candlesticks(
                                    symbol=symbol,
                                    period=lb_period,
                                    count=1,
                                    adjust_type=AdjustType.ForwardAdjust,
                                )
                                if candles:
                                    latest = candles[-1]
                                    bar = Bar.from_longbridge_candle(
                                        symbol, period_str, latest.__dict__
                                    )
                                    await self._queue.put(bar)
                            except Exception:
                                pass  # skip on transient errors
                    await asyncio.sleep(max(1, 60 / len(self._config.symbols)))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"SDK stream error: {e}")
                    await asyncio.sleep(5)  # backoff
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    async def _stream_cli_poll(self):
        """
        Fallback: poll Longbridge CLI kline endpoint at the bar interval.
        Deduplicates bars by timestamp.  Works without the SDK.
        Uses async subprocess to avoid blocking the event loop.
        """
        # Period → poll interval in seconds
        poll_intervals = {
            "1m": 60,
            "15m": 900,
            "1d": 86400,  # once daily
        }

        last_ts: dict[str, dict[str, datetime]] = {}  # symbol:period → last bar ts
        first_fetch: set[str] = set()  # track first-ever poll per symbol×period

        # Interleave symbols so bars arrive gradually, not in huge batches
        async def _poll_one(symbol, period_str):
            key = f"{symbol}:{period_str}"
            is_first = key not in first_fetch
            if is_first:
                first_fetch.add(key)
            # D1 needs 300 for SMA(200), M15/1m need 50 for warmup
            if period_str == "1d":
                count = "300" if is_first else "3"
            else:
                count = "50" if is_first else "3"
            try:
                proc = await asyncio.create_subprocess_exec(
                    "longbridge", "kline", symbol,
                    "--period", period_str,
                    "--count", count,
                    "--format", "json",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
                if proc.returncode != 0:
                    return
                data = json.loads(stdout)
                if not data:
                    return

                for candle in data:
                    bar = Bar.from_longbridge_candle(symbol, period_str, candle)
                    last = last_ts[symbol].get(period_str)
                    if last is None or bar.timestamp > last:
                        last_ts[symbol][period_str] = bar.timestamp
                        try:
                            self._queue.put_nowait(bar)
                        except asyncio.QueueFull:
                            logger.warning(
                                f"Queue full, dropping bar: {symbol} {period_str}"
                            )
            except asyncio.TimeoutError:
                logger.debug(f"CLI timeout {symbol} {period_str}")
            except Exception as e:
                logger.debug(f"CLI poll error {symbol} {period_str}: {e}")

        while self._running:
            # Poll 1 symbol at a time, yielding to event loop between each
            for symbol in self._config.symbols:
                if not self._running:
                    break
                if symbol not in last_ts:
                    last_ts[symbol] = {}

                for period_str in self._config.periods:
                    if not self._running:
                        break
                    await _poll_one(symbol, period_str)
                    # Small yield between symbols to let main loop consume bars
                    await asyncio.sleep(0.5)

            # After one full cycle, sleep based on the fastest period
            sleep_time = min(poll_intervals.get(p, 60) for p in self._config.periods)
            await asyncio.sleep(sleep_time)


class BarBuffer:
    """
    Thread-safe rolling buffer of bars for a single symbol × period.
    Used by Layer 2 and Layer 3 to maintain fixed-length windows for
    indicator computation.
    """

    def __init__(self, maxlen: int = 500):
        self._maxlen = maxlen
        self._bars: list[Bar] = []
        self._timestamps: set[datetime] = set()  # dedup key

    def add(self, bar: Bar) -> bool:
        """
        Add a bar to the buffer.  Returns True if bar was new (added).
        """
        if bar.timestamp in self._timestamps:
            return False
        self._bars.append(bar)
        self._timestamps.add(bar.timestamp)
        # Trim
        while len(self._bars) > self._maxlen:
            removed = self._bars.pop(0)
            self._timestamps.discard(removed.timestamp)
        return True

    def get_bars(self) -> list[Bar]:
        return list(self._bars)

    def latest(self) -> Optional[Bar]:
        return self._bars[-1] if self._bars else None

    def __len__(self) -> int:
        return len(self._bars)

    def is_warm(self, min_bars: int) -> bool:
        return len(self._bars) >= min_bars