"""
Dynamic trailing stop manager for live positions.

Updates on every M1 bar:
  - Tracks highest price (LONG) / lowest price (SHORT) since entry
  - Computes trailing stop = highest_price - (ATR_mult × ATR15)
  - Micro-volatility adjusts the ATR multiplier dynamically
  - Regime-based tightening (choppy → tighter stops)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .micro_volatility import MicroMetrics, compute_trail_multiplier

logger = logging.getLogger(__name__)


@dataclass
class TrailState:
    """Trailing stop state for one active position."""
    symbol: str
    side: str                       # "LONG" or "SHORT"
    entry_price: float
    initial_stop: float
    atr15: float                    # ATR(15) from Layer 2

    # Dynamic state
    highest_price: float = 0.0      # Highest since entry (LONG)
    lowest_price: float = 0.0       # Lowest since entry (SHORT)
    current_trail: float = 0.0      # Current trailing stop price
    current_mult: float = 1.5       # Current ATR multiplier
    trail_adjusted_count: int = 0   # How many times multiplier has changed

    # Time tracking
    entry_time: Optional[datetime] = None
    bars_held: int = 0
    flat_bars: int = 0              # Consecutive bars with no move > threshold
    cumulative_move: float = 0.0    # Total move since entry (abs %)

    # Exit flags
    stop_breached: bool = False
    time_decay_exit: bool = False
    exit_reason: str = ""


class TrailingStopManager:
    """
    Manages trailing stops for all active positions.

    Called on every M1 bar.  Emits exit signals when stop is breached
    or time-decay limits are reached.
    """

    def __init__(
        self,
        base_trail_mult: float = 1.5,
        tighten_mult: float = 0.75,
        loosen_mult: float = 2.0,
        tighten_vol_z: float = 2.5,
        loosen_vol_z: float = -1.5,
        max_flat_hours: float = 5.0,
        min_move_pct: float = 0.002,
    ):
        self._base_mult = base_trail_mult
        self._tighten_mult = tighten_mult
        self._loosen_mult = loosen_mult
        self._tighten_z = tighten_vol_z
        self._loosen_z = loosen_vol_z
        self._max_flat_mins = int(max_flat_hours * 60)
        self._min_move_pct = min_move_pct

        self._states: dict[str, TrailState] = {}

    # ── Position lifecycle ─────────────────────────────────────────────

    def register_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        initial_stop: float,
        atr15: float,
        entry_time: Optional[datetime] = None,
    ):
        """Register a new position for trailing stop tracking."""
        state = TrailState(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            initial_stop=initial_stop,
            atr15=atr15,
            highest_price=entry_price,
            lowest_price=entry_price,
            current_trail=initial_stop,
            current_mult=self._base_mult,
            entry_time=entry_time or datetime.now(timezone.utc),
        )
        self._states[symbol] = state
        logger.info(
            f"Trail registered: {symbol} {side} entry={entry_price:.2f} "
            f"stop={initial_stop:.2f} ATR={atr15:.2f}"
        )

    def unregister_position(self, symbol: str):
        """Remove position from tracking (after exit)."""
        if symbol in self._states:
            del self._states[symbol]
            logger.info(f"Trail unregistered: {symbol}")

    # ── Per-bar update ─────────────────────────────────────────────────

    def update(
        self,
        symbol: str,
        current_price: float,
        current_high: float,
        current_low: float,
        micro_metrics: MicroMetrics,
        regime_tighten: bool = False,
    ) -> TrailState:
        """
        Update trailing stop for a symbol on a new M1 bar.

        Args:
            symbol: Ticker
            current_price: Latest M1 bar close
            current_high: Latest M1 bar high
            current_low: Latest M1 bar low
            micro_metrics: Micro-volatility metrics
            regime_tighten: True if regime says tighten stops (choppy/risk_off)

        Returns:
            Updated TrailState (check .stop_breached and .time_decay_exit)
        """
        state = self._states.get(symbol)
        if state is None:
            logger.warning(f"Trail update for unknown position: {symbol}")
            return TrailState(symbol=symbol, side="LONG", entry_price=0, initial_stop=0, atr15=0)

        state.bars_held += 1

        # Update extreme price
        if state.side == "LONG":
            if current_high > state.highest_price:
                state.highest_price = current_high
                state.flat_bars = 0  # reset flat counter on new high
            else:
                state.flat_bars += 1

            # Compute trail multiplier from micro metrics
            trail_mult = compute_trail_multiplier(
                micro_metrics,
                base_mult=self._base_mult,
                tighten_z=self._tighten_z,
                loosen_z=self._loosen_z,
                tighten_mult=self._tighten_mult,
                loosen_mult=self._loosen_mult,
            )

            # Regime override: tighten further
            if regime_tighten:
                trail_mult = min(trail_mult, self._tighten_mult)

            state.current_mult = trail_mult

            # Trailing stop = highest_price - (trail_mult × ATR)
            new_trail = state.highest_price - (trail_mult * state.atr15)

            # Only move stop UP for LONG positions (ratchet)
            if new_trail > state.current_trail:
                state.current_trail = round(new_trail, 2)
                state.trail_adjusted_count += 1

            # Check breach
            if current_low <= state.current_trail:
                state.stop_breached = True
                state.exit_reason = f"trailing stop hit at {state.current_trail:.2f}"

        else:  # SHORT
            if current_low < state.lowest_price:
                state.lowest_price = current_low
                state.flat_bars = 0
            else:
                state.flat_bars += 1

            trail_mult = compute_trail_multiplier(
                micro_metrics,
                base_mult=self._base_mult,
                tighten_z=self._tighten_z,
                loosen_z=self._loosen_z,
                tighten_mult=self._tighten_mult,
                loosen_mult=self._loosen_mult,
            )
            if regime_tighten:
                trail_mult = min(trail_mult, self._tighten_mult)

            state.current_mult = trail_mult

            # Trailing stop = lowest_price + (trail_mult × ATR)
            new_trail = state.lowest_price + (trail_mult * state.atr15)

            # Only move stop DOWN for SHORT positions
            if new_trail < state.current_trail or state.current_trail == 0:
                state.current_trail = round(new_trail, 2)
                state.trail_adjusted_count += 1

            if current_high >= state.current_trail:
                state.stop_breached = True
                state.exit_reason = f"trailing stop hit at {state.current_trail:.2f}"

        # Time decay check
        if not state.stop_breached and state.flat_bars >= self._max_flat_mins:
            # Check cumulative move
            entry = state.entry_price
            if entry > 0:
                total_move = abs(current_price - entry) / entry
                state.cumulative_move = total_move
                if total_move < self._min_move_pct:
                    state.time_decay_exit = True
                    state.exit_reason = (
                        f"time decay: flat for {state.flat_bars} min, "
                        f"move={total_move:.2%}"
                    )

        return state

    def get_state(self, symbol: str) -> Optional[TrailState]:
        return self._states.get(symbol)

    @property
    def all_states(self) -> dict[str, TrailState]:
        return dict(self._states)

    @property
    def active_positions(self) -> list[str]:
        return list(self._states.keys())