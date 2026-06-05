"""
Four-Level FX Strategy — NautilusTrader port of fx_daemon.py
============================================================
Ports the 4-level signal engine (SMA structure -> EMA momentum -> RSI exhaustion)
and the ATR protective stop from the original asyncio daemon into a
NautilusTrader Strategy.

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
from nautilus_trader.model.position import Position
from nautilus_trader.trading.strategy import Strategy


class FourLevelConfig(StrategyConfig, frozen=True):
    # Bar types, e.g. "EUR/USD.IDEALPRO-1-HOUR-MID-EXTERNAL"
    bar_types: list[str]
    # Per-instrument trade size in units, keyed by instrument_id string
    position_sizes: dict[str, int]
    # ATR stop multiplier keyed by instrument_id string (trend 5.0, carry 10.0)
    atr_multipliers: dict[str, float]
    atr_period: int = 14
    rsi_period: int = 14
    min_confidence: int = 2
    # GTC so a daemon/gateway outage cannot strip your protective stop.
    # (Review note: the old code used tif='DAY', which expires overnight and
    #  leaves positions naked if the host is down at session roll.)
    stop_tif: TimeInForce = TimeInForce.GTC


class _InstState:
    """Per-instrument indicator bundle + the previous-bar SMAs needed for the
    golden/death-cross detection in the original compute_level()."""

    __slots__ = (
        "sma20", "sma50", "ema20", "ema50", "rsi", "atr",
        "prev_s20", "prev_s50", "stop_attached",
    )

    def __init__(self, rsi_period: int, atr_period: int):
        self.sma20 = SimpleMovingAverage(20)
        self.sma50 = SimpleMovingAverage(50)
        self.ema20 = ExponentialMovingAverage(20)
        self.ema50 = ExponentialMovingAverage(50)
        self.rsi = RelativeStrengthIndex(rsi_period)
        self.atr = AverageTrueRange(atr_period)
        self.prev_s20: float | None = None
        self.prev_s50: float | None = None
        self.stop_attached: bool = False


class FourLevelStrategy(Strategy):
    def __init__(self, config: FourLevelConfig):
        super().__init__(config)
        self._state: dict[InstrumentId, _InstState] = {}
        self._bar_types: dict[InstrumentId, BarType] = {}
        self._latest: dict[InstrumentId, dict] = {}          # last signal per pair (for /status)
        self._state_path = os.environ.get("STATE_PATH")      # set in live deploy; unset in backtest
        self._control_path = os.environ.get("CONTROL_PATH")  # switches the Telegram bot writes
        self._trades_path = os.environ.get("TRADES_PATH")    # append-only trade log for /history
        self._state_interval = int(os.environ.get("STATE_INTERVAL_SECS", "30"))
        self._snap_stop = threading.Event()
        self._snap_thread = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    def on_start(self):
        for bt_str in self.config.bar_types:
            bar_type = BarType.from_str(bt_str)
            iid = bar_type.instrument_id
            st = _InstState(self.config.rsi_period, self.config.atr_period)
            self._state[iid] = st
            self._bar_types[iid] = bar_type
            # Auto-update every indicator on each bar of this type
            for ind in (st.sma20, st.sma50, st.ema20, st.ema50, st.rsi, st.atr):
                self.register_indicator_for_bars(bar_type, ind)
            # Warm up indicators with ~10 days of 1h history (start is REQUIRED in 1.227), then stream live
            self.request_bars(bar_type, start=self.clock.utc_now() - timedelta(days=10))
            self.subscribe_bars(bar_type)
            self.log.info(f"Subscribed {bar_type}")
        self._notify("🟢 ATS FX strategy started (paper) — " + ", ".join(str(i) for i in self._bar_types))
        if self._state_path:
            self._write_state()              # initial snapshot
            self._start_snapshot_thread()    # refresh every _state_interval seconds

    def on_stop(self):
        self._snap_stop.set()                # stop the snapshot thread
        # Cancel resting working orders, but deliberately do NOT flatten:
        # the protective GTC stops stay live at IB so a restart can't leave you naked.
        for iid in self._state:
            self.cancel_all_orders(iid)

    # ── core decision loop ─────────────────────────────────────────────────
    def on_bar(self, bar: Bar):
        iid = bar.bar_type.instrument_id
        st = self._state.get(iid)
        if st is None or not self._ready(st):
            return

        level, rsi = self._compute_level(st, float(bar.close))
        signal, conf = self._derive_signal(level, rsi)

        self._latest[iid] = {
            "level": level, "rsi": round(rsi, 1), "signal": signal, "confidence": conf,
            "price": round(float(bar.close), 5), "ts": self.clock.utc_now().isoformat(),
        }
        self._write_state()

        net = self.portfolio.net_position(iid)  # Decimal: >0 long, <0 short, 0 flat

        # 1) Manage exits on an existing position first (level-system signal exit).
        #    The trailing stop handles the price-based exit independently.
        if net != 0:
            self._manage_exit(iid, net, level, rsi)
            return  # at most one position action per bar

        # 2) Entries (only when flat). Respect the /pause and per-pair switches.
        if signal in ("LONG", "SHORT") and conf >= self.config.min_confidence:
            ctrl = self._load_control()
            if not ctrl["trading_enabled"]:
                self.log.info(f"Entry skipped {iid}: trading paused")
                return
            if not ctrl["pairs"].get(str(iid), True):
                self.log.info(f"Entry skipped {iid}: pair disabled")
                return
            self._enter(iid, signal, st, bar)

    # ── signal engine (ported from fx_daemon.compute_level / analyze_pair) ──
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

    def on_position_opened(self, event):
        # Attach the ATR trailing stop once the entry actually fills.
        iid = event.instrument_id
        st = self._state.get(iid)
        if st is None or st.stop_attached:
            return
        instrument = self.cache.instrument(iid)
        net = self.portfolio.net_position(iid)
        if net == 0 or instrument is None:
            return
        mult = self.config.atr_multipliers.get(str(iid), 5.0)
        offset = round(st.atr.value * mult, instrument.price_precision)
        stop_side = OrderSide.SELL if net > 0 else OrderSide.BUY
        trailing = self.order_factory.trailing_stop_market(
            instrument_id=iid,
            order_side=stop_side,
            quantity=instrument.make_qty(int(abs(net))),
            trailing_offset=Decimal(str(offset)),
            trailing_offset_type=TrailingOffsetType.PRICE,
            # FX has no trades — trigger on bid/ask. Switch to LAST_PRICE for
            # instruments that print last trades (e.g. equities via Longbridge).
            trigger_type=TriggerType.BID_ASK,
            time_in_force=self.config.stop_tif,
            reduce_only=True,
        )
        st.stop_attached = True
        self.submit_order(trailing)
        self.log.info(f"Trailing stop attached {iid}: offset {offset} ({mult}x ATR)")

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
        """Snapshot live state to STATE_PATH for the Telegram bot. No-op if unset; never raises."""
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
            stops = {}
            for o in self.cache.orders_open():
                tp = getattr(o, "trigger_price", None)
                if tp is not None:
                    stops[str(o.instrument_id)] = str(tp)
            account = []
            try:
                # The IB account is under the "IB" venue, not IDEALPRO — read all accounts.
                for acct in self.cache.accounts():
                    account += [str(v) for v in acct.balances_total().values()]
            except Exception:
                pass
            ctrl = self._load_control()
            snapshot = {
                "ts": self.clock.utc_now().isoformat(),
                "trading_enabled": ctrl["trading_enabled"],
                "pairs": ctrl["pairs"],
                "signals": signals,
                "positions": positions,
                "stops": stops,
                "account": account,
            }
            tmp = self._state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(snapshot, f, default=str)
            os.replace(tmp, self._state_path)
        except Exception as exc:
            self.log.warning(f"state snapshot failed: {exc}")

    # ── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _ready(st: _InstState) -> bool:
        return all(i.initialized for i in (st.sma50, st.ema50, st.rsi, st.atr))

    def _open_position(self, iid: InstrumentId) -> Position | None:
        positions = self.cache.positions_open(instrument_id=iid)
        return positions[0] if positions else None
