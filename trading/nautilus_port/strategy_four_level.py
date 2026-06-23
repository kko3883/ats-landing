"""
Four-Level FX Strategy — NautilusTrader port of fx_daemon.py
============================================================
Ports the 4-level signal engine (SMA structure -> EMA momentum -> RSI exhaustion)
and the ATR protective stop from the original asyncio daemon into a
NautilusTrader Strategy.

v2: Sliding 1H evaluation + fast trigger
    - Subscribes to 15-minute bars instead of 1-hour bars.
    - Builds a trailing-60-minute (sliding) 1H bar from the last four 15m bars
      on every 15m close.
    - Hourly indicators (SMA20/50, EMA20/50, RSI14, ATR14) are computed on the
      series of sliding 1H bars — evaluates the same hourly mechanism 4x/hour.
    - Fast trigger gate (Layer 2) confirms 15m momentum aligns with hourly
      permission before firing an entry.
    - Debounce prevents re-entry churn from sliding-window cross flicker.

WHAT NAUTILUS GIVES YOU (that the daemon hand-rolled and got wrong):
  * Order/position state machine + startup reconciliation -> kills the
    "wrong-way market order on an already-flat position" bug (review #1).
  * Native TrailingStopMarketOrder                         -> replaces the manual
    ATR trailing + orphan-stop-on-stacked-entry bug (review #2).
  * The cache is the single owner of position state        -> no aliasing fragility.
  * Same code runs in backtest AND live                    -> validate before risking money.

The signal logic below is copied as faithfully as possible from
fx_daemon.compute_level() / analyze_pair() / manage_positions_async() so you can
diff behaviour 1:1 against the old daemon during the parallel-run phase.

NOTE: Validated against nautilus_trader 1.227.0 — imports, config, indicators, enums,
the order-factory trailing-stop signature, and the position-event hooks all load clean.
Nautilus is Beta, so re-verify if you bump the version. Runtime behaviour (live fills,
reconciliation) still needs a paper run before you trust it.
"""
import json
import os
import threading
import urllib.parse
import urllib.request
from collections import deque
from datetime import timedelta
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators.averages import (
    ExponentialMovingAverage,
    SimpleMovingAverage,
)
from nautilus_trader.indicators.momentum import RelativeStrengthIndex
from nautilus_trader.indicators.volatility import AverageTrueRange
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import (
    OrderSide,
    TimeInForce,
    TrailingOffsetType,
    TriggerType,
)
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.trading.strategy import Strategy


class FourLevelConfig(StrategyConfig, frozen=True):
    # Bar types — now 15m (e.g. "EUR.USD-15-MINUTE-MID-EXTERNAL")
    bar_types: list[str]
    # Per-instrument trade size in units, keyed by instrument_id string
    position_sizes: dict[str, int]
    # ATR stop multiplier keyed by instrument_id string (trend 5.0, carry 10.0)
    atr_multipliers: dict[str, float]
    atr_period: int = 14
    rsi_period: int = 14
    min_confidence: int = 2
    # GTC so a daemon/gateway outage cannot strip your protective stop.
    stop_tif: TimeInForce = TimeInForce.GTC

    # ── Sliding-window & fast-trigger config (v2) ─────────────────────────
    bar_interval_minutes: int = 15
    sliding_window_bars: int = 4          # 4 x 15m = 60 min sliding 1H
    buffer_maxlen: int = 256
    history_prefill_bars: int = 300
    fast_trigger_enabled: bool = True
    fast_trigger_ema_period: int = 8      # on 15m closes
    fast_trigger_rsi_guard: bool = True
    fast_trigger_rsi_high: int = 75
    fast_trigger_rsi_low: int = 25
    min_bars_between_entries: int = 1


class _InstState:
    """Per-instrument indicator bundle + sliding-1H buffers + debounce state."""

    __slots__ = (
        # 15m bar buffer (fixed-length deque, oldest bar automatically discarded)
        "buffer_15m",
        # BarType of the 15m subscription (used for building synthetic 1H bars)
        "bar_type_15m",
        # Hourly indicators — fed synthetic sliding-1H bars
        "sma20", "sma50", "ema20", "ema50", "rsi", "atr",
        "prev_s20", "prev_s50",
        "stop_attached",
        "stop_price",
        # Fast-trigger (15m) indicators — fed actual 15m bars
        "ft_ema",               # ExponentialMovingAverage on 15m closes
        "ft_rsi",               # RelativeStrengthIndex on 15m closes
        # Warmup
        "is_warm",
        # Debounce
        "last_signal_state",        # "LONG" / "SHORT" / "FLAT" — signal from previous eval
        "last_entry_direction",     # "LONG" / "SHORT" — direction of last fired entry
        "signal_flipped_to_neutral",# True if signal went FLAT/opposite since last entry
        "bars_since_last_entry",    # count of 15m evaluations since last entry
    )

    def __init__(
        self,
        rsi_period: int,
        atr_period: int,
        buffer_maxlen: int,
        ft_ema_period: int,
        bar_type_15m: BarType,
    ):
        self.buffer_15m: deque[Bar] = deque(maxlen=buffer_maxlen)
        self.bar_type_15m = bar_type_15m
        # Hourly indicators (on sliding-1H)
        self.sma20 = SimpleMovingAverage(20)
        self.sma50 = SimpleMovingAverage(50)
        self.ema20 = ExponentialMovingAverage(20)
        self.ema50 = ExponentialMovingAverage(50)
        self.rsi = RelativeStrengthIndex(rsi_period)
        self.atr = AverageTrueRange(atr_period)
        self.prev_s20: float | None = None
        self.prev_s50: float | None = None
        self.stop_attached: bool = False
        self.stop_price: float = 0.0        # trailing stop trigger price (for /status display)
        # Fast trigger (15m)
        self.ft_ema = ExponentialMovingAverage(ft_ema_period)
        self.ft_rsi = RelativeStrengthIndex(rsi_period)
        # Warmup
        self.is_warm: bool = False
        # Debounce
        self.last_signal_state: str = "FLAT"
        self.last_entry_direction: str | None = None
        self.signal_flipped_to_neutral: bool = True
        self.bars_since_last_entry: int = 9999


class FourLevelStrategy(Strategy):
    def __init__(self, config: FourLevelConfig):
        super().__init__(config)
        self._state: dict[InstrumentId, _InstState] = {}
        self._bar_types: dict[InstrumentId, BarType] = {}
        self._latest: dict[InstrumentId, dict] = {}          # last signal per pair (for /status)
        self._quotes: dict[InstrumentId, dict] = {}           # latest 15m bar data (rt quote proxy)
        self._state_path = os.environ.get("STATE_PATH")      # set in live deploy; unset in backtest
        self._control_path = os.environ.get("CONTROL_PATH")  # switches the Telegram bot writes
        self._trades_path = os.environ.get("TRADES_PATH")    # append-only trade log for /history
        self._state_interval = int(os.environ.get("STATE_INTERVAL_SECS", "30"))
        self._snap_stop = threading.Event()
        self._snap_thread = None
        self._stops_pending_on_warm: bool = True   # always true on startup — attach stops on first warm eval
        # Observability counters (added to state.json, non-breaking)
        self._counters: dict[str, int] = {
            "evals_total": 0,
            "signals_raw": 0,
            "blocked_by_debounce": 0,
            "blocked_by_fast_trigger": 0,
            "entries_fired": 0,
        }

    # ── lifecycle ──────────────────────────────────────────────────────────
    def on_start(self):
        for bt_str in self.config.bar_types:
            bar_type = BarType.from_str(bt_str)
            iid = bar_type.instrument_id
            st = _InstState(
                rsi_period=self.config.rsi_period,
                atr_period=self.config.atr_period,
                buffer_maxlen=self.config.buffer_maxlen,
                ft_ema_period=self.config.fast_trigger_ema_period,
                bar_type_15m=bar_type,
            )
            self._state[iid] = st
            self._bar_types[iid] = bar_type
            # Request historical 15m bars to pre-fill the buffer.
            # 300 bars * 15 min = 75 h ≈ 3.125 days; use 4 days for safety margin.
            prefill_start = self.clock.utc_now() - timedelta(days=4)
            self.request_bars(bar_type, start=prefill_start)
            self.subscribe_bars(bar_type)
            self.log.info(f"Subscribed {bar_type} (15m, sliding-1H eval)")

        self._notify("🟢 ATS FX strategy started (paper, sliding-1H) — "
                     + ", ".join(str(i) for i in self._bar_types))

        if self._state_path:
            self._write_state()              # initial snapshot
            self._start_snapshot_thread()    # refresh every _state_interval seconds

    def on_stop(self):
        self._snap_stop.set()                # stop the snapshot thread
        # Cancel resting working orders, but deliberately do NOT flatten:
        # the protective GTC stops stay live at IB so a restart can't leave you naked.
        for iid in self._state:
            self.cancel_all_orders(iid)

    # ── handlers that catch ALL bar data (streaming + historical) ──────────
    def handle_historical_bar(self, bar: Bar):
        """Nautilus 1.227 delivers each historical bar from request_bars()
        through this handler. Base handle_data dispatches BarData -> here."""
        self._process_bar(bar)

    def on_bar(self, bar: Bar):
        """Called for live streaming 15m bars after subscription is active.
        Base handle_data dispatches live Bar objects -> here."""
        self._process_bar(bar)

    def _process_bar(self, bar: Bar):
        iid = bar.bar_type.instrument_id
        st = self._state.get(iid)
        if st is None:
            return

        # 1. Capture real-time quote from the latest 15m bar (high/low/close for /status)
        self._quotes[iid] = {
            "bid": round(float(bar.low), 5),
            "ask": round(float(bar.high), 5),
            "mid": round((float(bar.high) + float(bar.low)) / 2, 5),
            "close": round(float(bar.close), 5),
            "ts": self.clock.utc_now().isoformat(),
        }

        # 2. Append 15m bar to the rolling buffer
        st.buffer_15m.append(bar)

        # 3. Update fast-trigger (15m) indicators with the actual 15m bar
        st.ft_ema.handle_bar(bar)
        st.ft_rsi.handle_bar(bar)

        # 3. Warmup check — need enough 15m bars to seed the sliding-1H indicators
        if not st.is_warm:
            if self._check_warmup(st):
                st.is_warm = True
                self.log.info(f"Warmup complete for {iid} "
                              f"(buffer: {len(st.buffer_15m)} 15m bars)")
            else:
                return  # still cold, silently ingest

        # 4. Build sliding-1H bar from the trailing N 15m bars
        bar_1h = self._build_sliding_1h(st, bar)
        if bar_1h is None:
            return

        # 5. Feed the synthetic sliding-1H bar to hourly indicators
        st.sma20.handle_bar(bar_1h)
        st.sma50.handle_bar(bar_1h)
        st.ema20.handle_bar(bar_1h)
        st.ema50.handle_bar(bar_1h)
        st.rsi.handle_bar(bar_1h)
        st.atr.handle_bar(bar_1h)

        # Re-check indicator readiness after feeding (indicators may need more bars)
        if not self._indicators_ready(st):
            return

        # After each warm evaluation, try to attach stops to any pre-existing positions
        # that were reconciled from IB on startup (they missed on_position_opened).
        # Keep retrying until all positions are covered, since different instruments
        # warm up at different times.
        if self._stops_pending_on_warm:
            self._attach_stops_to_existing()
            # Check if any open position still lacks a stop
            still_pending = False
            for pos in self.cache.positions_open():
                iid = pos.instrument_id
                s = self._state.get(iid)
                if s and not s.stop_attached:
                    still_pending = True
                    break
            if not still_pending:
                self._stops_pending_on_warm = False

        # 6. Compute level and raw signal (EXACT same logic as original)
        level, rsi = self._compute_level(st, float(bar_1h.close))
        signal, conf = self._derive_signal(level, rsi)

        # 7. Observability counters
        self._counters["evals_total"] += 1
        st.bars_since_last_entry += 1

        # 8. Update latest state per instrument (for /status)
        self._latest[iid] = {
            "level": level, "rsi": round(rsi, 1), "signal": signal, "confidence": conf,
            "price": round(float(bar_1h.close), 5), "ts": self.clock.utc_now().isoformat(),
        }
        self._write_state()

        # Log every evaluation at DEBUG
        self.log.debug(
            f"Eval {iid}: close={float(bar_1h.close):.5f} "
            f"L{level} RSI={rsi:.1f} signal={signal} conf={conf} "
            f"s20={st.sma20.value:.5f} s50={st.sma50.value:.5f} "
            f"e20={st.ema20.value:.5f} e50={st.ema50.value:.5f} "
            f"atr={st.atr.value:.5f}"
        )

        net = self.portfolio.net_position(iid)  # Decimal: >0 long, <0 short, 0 flat

        # 9) Manage exits on an existing position first (level-system signal exit).
        #    The trailing stop handles the price-based exit independently.
        #    EXITS ARE NOT DEBOUNCED — always act immediately.
        if net != 0:
            self._manage_exit(iid, net, level, rsi)
            return  # at most one position action per bar

        # 10) Entries (only when flat). Respect the /pause and per-pair switches.
        if signal in ("LONG", "SHORT") and conf >= self.config.min_confidence:
            ctrl = self._load_control()
            if not ctrl["trading_enabled"]:
                self.log.info(f"Entry skipped {iid}: trading paused")
                return
            if not ctrl["pairs"].get(str(iid), True):
                self.log.info(f"Entry skipped {iid}: pair disabled")
                return

            self._counters["signals_raw"] += 1

            # Debounce: prevent re-entry churn from sliding-window cross flicker
            if not self._check_debounce(st, signal):
                self._counters["blocked_by_debounce"] += 1
                self.log.debug(
                    f"Debounce blocked {iid}: {signal} "
                    f"(last_entry={st.last_entry_direction}, "
                    f"flipped={st.signal_flipped_to_neutral}, "
                    f"bars_since={st.bars_since_last_entry})"
                )
                return

            # Fast trigger gate (Layer 2): confirm 15m momentum aligns
            if self.config.fast_trigger_enabled:
                if not self._check_fast_trigger(st, signal):
                    self._counters["blocked_by_fast_trigger"] += 1
                    self.log.debug(
                        f"Fast-trigger blocked {iid}: {signal} "
                        f"(15m close={float(bar.close):.5f} "
                        f"ft_ema={st.ft_ema.value:.5f} "
                        f"ft_rsi={st.ft_rsi.value:.1f})"
                    )
                    return

            # All gates passed — fire entry
            self._counters["entries_fired"] += 1
            st.last_entry_direction = signal
            st.signal_flipped_to_neutral = False
            st.bars_since_last_entry = 0
            self._enter(iid, signal, st, bar_1h)

    # ── sliding-1H construction ────────────────────────────────────────────
    def _build_sliding_1h(self, st: _InstState, latest_bar: Bar) -> Bar | None:
        """Build a synthetic trailing-60-minute bar from the last N 15m bars."""
        buf = st.buffer_15m
        window_n = self.config.sliding_window_bars
        if len(buf) < window_n:
            return None

        # Extract the trailing window (list from deque)
        window = list(buf)[-window_n:]

        open_p = window[0].open
        high_p = max(b.high for b in window)
        low_p = min(b.low for b in window)
        close_p = window[-1].close
        # Sum volume across the four 15m bars
        vol_sum = sum(int(b.volume) for b in window)
        ts_event = window[-1].ts_event
        ts_init = latest_bar.ts_init

        return Bar(
            bar_type=st.bar_type_15m,  # re-use 15m bar_type; indicators only read OHLCV
            open=open_p,
            high=high_p,
            low=low_p,
            close=close_p,
            volume=Quantity(vol_sum, precision=0),
            ts_event=ts_event,
            ts_init=ts_init,
        )

    # ── warmup ─────────────────────────────────────────────────────────────
    def _check_warmup(self, st: _InstState) -> bool:
        """Return True when enough 15m bars are in the buffer + hourly indicators
        have enough sliding-1H points to be initialized."""
        buf = st.buffer_15m
        window_n = self.config.sliding_window_bars

        # Need at least window_n bars to build one sliding-1H bar
        if len(buf) < window_n:
            return False

        # Build all available sliding-1H bars from the buffer and feed to indicators.
        # This catches up historical bars that arrived via request_bars().
        # We feed sliding-1H bars sequentially to warm the indicators.
        for i in range(window_n, len(buf) + 1):
            window = list(buf)[i - window_n:i]
            bar_1h = Bar(
                bar_type=st.bar_type_15m,
                open=window[0].open,
                high=max(b.high for b in window),
                low=min(b.low for b in window),
                close=window[-1].close,
                volume=Quantity(sum(int(b.volume) for b in window), precision=0),
                ts_event=window[-1].ts_event,
                ts_init=window[-1].ts_init,
            )
            st.sma20.handle_bar(bar_1h)
            st.sma50.handle_bar(bar_1h)
            st.ema20.handle_bar(bar_1h)
            st.ema50.handle_bar(bar_1h)
            st.rsi.handle_bar(bar_1h)
            st.atr.handle_bar(bar_1h)

        # Check if the slowest indicator (SMA50) is initialized
        return self._indicators_ready(st)

    @staticmethod
    def _indicators_ready(st: _InstState) -> bool:
        """Return True when all hourly indicators are initialized."""
        return all(
            i.initialized
            for i in (st.sma50, st.ema50, st.rsi, st.atr)
        )

    # ── debounce (prevents sliding-window flicker) ─────────────────────────
    def _update_debounce_state(self, st: _InstState, signal: str):
        """Track whether signal has flipped to neutral/opposite since last entry.
        Called on every evaluation regardless of whether we act on the signal."""
        prev = st.last_signal_state
        st.last_signal_state = signal

        if st.last_entry_direction is None:
            return

        # A flip to HOLD or opposite direction resets the debounce gate
        if signal == "HOLD":
            st.signal_flipped_to_neutral = True
        elif st.last_entry_direction == "LONG" and signal == "SHORT":
            st.signal_flipped_to_neutral = True
        elif st.last_entry_direction == "SHORT" and signal == "LONG":
            st.signal_flipped_to_neutral = True

    def _check_debounce(self, st: _InstState, signal: str) -> bool:
        """Return True if entry should proceed, False to block.

        Rule: do not open a NEW position in the same direction unless the signal
        state has flipped to FLAT (or opposite) since the last entry AND at least
        MIN_BARS_BETWEEN_ENTRIES bars have passed.
        """
        # Update debounce state tracking first
        self._update_debounce_state(st, signal)

        # First entry ever — always allow
        if st.last_entry_direction is None:
            return True

        # Different direction — always allow
        if signal != st.last_entry_direction:
            return True

        # Same direction — require intervening state change + min bars
        if not st.signal_flipped_to_neutral:
            return False
        if st.bars_since_last_entry < self.config.min_bars_between_entries:
            return False
        return True

    # ── fast trigger (Layer 2 — confirmation gate) ─────────────────────────
    def _check_fast_trigger(self, st: _InstState, signal: str) -> bool:
        """Return True if 15m momentum confirms the hourly permission.

        LONG:  latest 15m close > 15m EMA(8); optionally 15m RSI(14) < 75
        SHORT: latest 15m close < 15m EMA(8); optionally 15m RSI(14) > 25
        """
        if not st.ft_ema.initialized:
            return True  # not enough data yet — allow (let hourly be the gate)

        ft_ema_val = st.ft_ema.value
        if ft_ema_val is None:
            return True

        # Get latest 15m close from the buffer
        if len(st.buffer_15m) == 0:
            return False
        last_close = float(st.buffer_15m[-1].close)

        if signal == "LONG":
            if last_close <= ft_ema_val:
                return False
            if self.config.fast_trigger_rsi_guard and st.ft_rsi.initialized:
                if st.ft_rsi.value is not None and st.ft_rsi.value >= self.config.fast_trigger_rsi_high:
                    return False
        elif signal == "SHORT":
            if last_close >= ft_ema_val:
                return False
            if self.config.fast_trigger_rsi_guard and st.ft_rsi.initialized:
                if st.ft_rsi.value is not None and st.ft_rsi.value <= self.config.fast_trigger_rsi_low:
                    return False

        return True

    # ── signal engine (ported from fx_daemon.compute_level / analyze_pair) ──
    #            *** EXACT same logic as v1 — UNCHANGED ***
    def _compute_level(self, st: _InstState, current: float) -> tuple[int, float]:
        s20, s50 = st.sma20.value, st.sma50.value
        e20, e50 = st.ema20.value, st.ema50.value
        rsi = st.rsi.value
        s20p, s50p = st.prev_s20, st.prev_s50

        trend_up = current > s20 and s20 > s50
        trend_down = current < s20 and s20 < s50
        momentum_up = current > e20 and e20 > e50
        momentum_down = current < e20 and e20 < e50
        sma_golden = s20p is not None and s20p <= s50p and s20 > s50
        sma_death = s20p is not None and s20p >= s50p and s20 < s50

        if trend_down and momentum_down and rsi < 30:
            level = -4
        elif trend_up and momentum_up and rsi > 70:
            level = 4
        elif momentum_down:
            level = -3
        elif momentum_up:
            level = 3
        elif trend_down:
            level = -2
        elif trend_up:
            level = 2
        elif sma_death:
            level = -1
        elif sma_golden:
            level = 1
        else:
            level = 0

        st.prev_s20, st.prev_s50 = s20, s50
        return level, rsi

    @staticmethod
    def _derive_signal(level: int, rsi: float) -> tuple[str, int]:
        signal, conf = "HOLD", 0
        if level >= 2 and rsi < 40:
            signal, conf = "LONG", min(level, 3)
        elif level <= -2 and rsi > 55:
            signal, conf = "SHORT", min(abs(level), 3)
        # Level 3 / -3 allow shallower pullback entries
        if level == 3 and 40 <= rsi < 50 and signal == "HOLD":
            signal, conf = "LONG", 3
        elif level == -3 and 45 < rsi <= 50 and signal == "HOLD":
            signal, conf = "SHORT", 3
        return signal, conf

    # ── exits ──────────────────────────────────────────────────────────────
    def _manage_exit(self, iid: InstrumentId, net, level: int, rsi: float):
        long = net > 0
        reason = None
        if long:
            if level <= 0:
                reason = f"trend faded to L{level}"
            elif level >= 4 or rsi > 70:
                reason = f"bull exhaustion (RSI {rsi:.0f})"
        else:
            if level >= 0:
                reason = f"trend recovered to L{level}"
            elif level <= -4 or rsi < 30:
                reason = f"bear exhaustion (RSI {rsi:.0f})"

        if reason:
            self.log.info(f"Signal exit {iid}: {reason}")
            # CRITICAL: cancel the protective trailing stop BEFORE closing, else it
            # rests on a soon-to-be-flat position and can fire later — the exact
            # wrong-way bug from the old daemon. Here it's one explicit call.
            self.cancel_all_orders(iid)
            pos = self._open_position(iid)
            if pos is not None:
                self.close_position(pos)

    # ── entries ────────────────────────────────────────────────────────────
    def _enter(self, iid: InstrumentId, signal: str, st: _InstState, bar: Bar):
        instrument = self.cache.instrument(iid)
        if instrument is None:
            self.log.warning(f"No instrument loaded for {iid}")
            return
        size = self.config.position_sizes.get(str(iid), 50_000)
        side = OrderSide.BUY if signal == "LONG" else OrderSide.SELL
        order = self.order_factory.market(
            instrument_id=iid,
            order_side=side,
            quantity=instrument.make_qty(size),
            time_in_force=TimeInForce.GTC,
        )
        st.stop_attached = False
        self.submit_order(order)
        self.log.info(f"Entry {signal} {size} {iid} @ ~{bar.close}")

    def _attach_stop(self, iid, st, pos, instrument, net):
        """Submit one ATR trailing stop for the given position. DRY shared by
        on_position_opened (new fills) and _attach_stops_to_existing (startup)."""
        mult = self.config.atr_multipliers.get(str(iid), 5.0)
        offset = round(st.atr.value * mult, instrument.price_precision)
        stop_side = OrderSide.SELL if net > 0 else OrderSide.BUY
        trailing = self.order_factory.trailing_stop_market(
            instrument_id=iid,
            order_side=stop_side,
            quantity=instrument.make_qty(int(abs(net))),
            trailing_offset=Decimal(str(offset)),
            trailing_offset_type=TrailingOffsetType.PRICE,
            trigger_type=TriggerType.BID_ASK,
            time_in_force=self.config.stop_tif,
            reduce_only=True,
        )
        entry_px = float(pos.avg_px_open)
        stop_px = round(entry_px - offset, instrument.price_precision) if net > 0 else round(entry_px + offset, instrument.price_precision)
        st.stop_price = stop_px
        st.stop_attached = True
        self.submit_order(trailing)
        self.log.info(f"Trailing stop attached {iid}: offset {offset} ({mult}x ATR)")

    def on_position_opened(self, event):
        iid = event.instrument_id
        st = self._state.get(iid)
        if st is None or st.stop_attached:
            return
        instrument = self.cache.instrument(iid)
        net = self.portfolio.net_position(iid)
        if net == 0 or instrument is None:
            return
        pos = self._open_position(iid)
        if pos is None:
            return
        self._attach_stop(iid, st, pos, instrument, net)

    def on_position_closed(self, event):
        iid = event.instrument_id
        st = self._state.get(iid)
        if st:
            st.stop_attached = False
        # Belt-and-suspenders: ensure no resting protective order survives a close.
        self.cancel_all_orders(iid)
        self.log.info(f"Position closed {iid}: realized PnL {event.realized_pnl}")
        self._notify(
            f"🛑 Closed {iid} @ {event.avg_px_close} | PnL {event.realized_pnl} "
            f"({event.realized_return:+.2%})"
        )
        self._append_trade({
            "type": "close", "pair": str(iid), "exit": str(event.avg_px_close),
            "pnl": str(event.realized_pnl), "return": f"{event.realized_return:+.2%}",
        })

    # ── notifications (Telegram) ─────────────────────────────────────────
    def on_order_filled(self, event):
        side = "BUY" if event.is_buy else "SELL"
        self._notify(f"✅ Filled {side} {event.last_qty} {event.instrument_id} @ {event.last_px}")
        self._append_trade({
            "type": "fill", "side": side, "qty": str(event.last_qty),
            "pair": str(event.instrument_id), "px": str(event.last_px),
        })

    def on_order_rejected(self, event):
        self.log.warning(f"Order rejected {event.instrument_id}: {event.reason}")
        self._notify(f"❌ Order REJECTED {event.instrument_id}: {event.reason}")

    def _notify(self, text: str):
        """Fire-and-forget Telegram message. No-op if env vars unset; never raises."""
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return

        def _send():
            try:
                data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/sendMessage", data=data
                )
                urllib.request.urlopen(req, timeout=10)
            except Exception as exc:
                self.log.warning(f"Telegram notify failed: {exc}")

        threading.Thread(target=_send, daemon=True).start()

    # ── state snapshot (for the Telegram bot) ────────────────────────────
    def _start_snapshot_thread(self):
        """Background loop: re-snapshot every _state_interval seconds. Used instead of a
        Nautilus clock timer (set_timer raises in on_start in this version)."""
        def _loop():
            while not self._snap_stop.wait(self._state_interval):
                self._write_state()
        self._snap_thread = threading.Thread(target=_loop, daemon=True)
        self._snap_thread.start()

    def _load_control(self) -> dict:
        """Read the bot's control switches. Fail-open (all enabled) if missing/unreadable —
        the switches are opt-in, so absence means 'trade normally'."""
        default = {"trading_enabled": True, "pairs": {}}
        if not self._control_path:
            return default
        try:
            with open(self._control_path) as f:
                c = json.load(f)
            return {
                "trading_enabled": bool(c.get("trading_enabled", True)),
                "pairs": dict(c.get("pairs", {})),
            }
        except Exception:
            return default

    def _append_trade(self, record: dict):
        """Append a trade event (fill/close) to the trades log for /history.
        No-op if unset; never raises."""
        if not self._trades_path:
            return
        try:
            record = {"ts": self.clock.utc_now().isoformat(), **record}
            with open(self._trades_path, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:
            self.log.warning(f"trade log append failed: {exc}")

    def _write_state(self):
        """Snapshot live state to STATE_PATH for the Telegram bot. No-op if unset; never raises.

        v2 additions: observability counters (non-breaking — ADDED, not removed/renamed).
        """
        if not self._state_path:
            return
        try:
            signals = {str(iid): v for iid, v in self._latest.items()}
            positions = []
            for pos in self.cache.positions_open():
                positions.append({
                    "pair": str(pos.instrument_id),
                    "side": "LONG" if pos.is_long else "SHORT",
                    "size": str(pos.quantity),
                    "entry": str(pos.avg_px_open),
                })
            stops = {
                str(iid): str(st.stop_price)
                for iid, st in self._state.items()
                if st.stop_attached and st.stop_price != 0.0
            }
            account = []
            try:
                # The IB account is under the "IB" venue, not IDEALPRO — read all accounts.
                for acct in self.cache.accounts():
                    account += [str(v) for v in acct.balances_total().values()]
            except Exception:
                pass
            quotes = {str(iid): v for iid, v in self._quotes.items()}
            ctrl = self._load_control()
            snapshot = {
                "ts": self.clock.utc_now().isoformat(),
                "trading_enabled": ctrl["trading_enabled"],
                "pairs": ctrl["pairs"],
                "quotes": quotes,
                "signals": signals,
                "positions": positions,
                "stops": stops,
                "account": account,
                # v2 observability counters (ADDED, non-breaking)
                "counters": self._counters,
            }
            tmp = self._state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(snapshot, f, default=str)
            os.replace(tmp, self._state_path)
        except Exception as exc:
            self.log.warning(f"state snapshot failed: {exc}")

    # ── stop-loss attachment for pre-existing positions ────────────────────
    def _attach_stops_to_existing(self):
        """Iterate over all open positions and attach ATR trailing stops if they
        don't already have one. Called once after the first warm evaluation
        to cover positions that were reconciled from IB on startup (which never
        fire on_position_opened)."""
        attached = 0
        for pos in self.cache.positions_open():
            iid = pos.instrument_id
            st = self._state.get(iid)
            if st is None or st.stop_attached:
                continue
            instrument = self.cache.instrument(iid)
            if instrument is None:
                continue
            net = self.portfolio.net_position(iid)
            if net == 0:
                continue
            # Check that ATR is initialized (should be by the time this runs)
            if not st.atr.initialized or st.atr.value is None or st.atr.value == 0:
                self.log.warning(
                    f"Cannot attach stop to pre-existing position {iid}: ATR not ready "
                    f"(initialized={st.atr.initialized}, value={st.atr.value})"
                )
                continue
            self._attach_stop(iid, st, pos, instrument, net)
            attached += 1
            self.log.info(
                f"Retroactive stop attached {iid}: stop {st.stop_price} "
                f"for pre-existing position size {net}"
            )
        if attached > 0:
            self._notify(f"🛡️ Attached protective stops to {attached} pre-existing position(s)")

    # ── helpers ────────────────────────────────────────────────────────────
    def _open_position(self, iid: InstrumentId) -> Position | None:
        positions = self.cache.positions_open(instrument_id=iid)
        return positions[0] if positions else None

    # _ready() removed — replaced by _check_warmup / _indicators_ready (v2)