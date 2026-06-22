"""
Exit manager — generates exit orders from Layer 3 signals.

Triggers:
  1. Trailing stop breach
  2. Time-decay (flat for > 5 hours with < 0.2% move)
  3. Regime crisis (exit all immediately)
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExitOrder:
    """An exit order ready for execution."""
    symbol: str
    side: str                    # "SELL" for closing LONG, "BUY" for closing SHORT
    exit_price: float            # Market price
    quantity: int
    exit_reason: str
    stop_price: float = 0.0      # The trailing stop that triggered (if applicable)
    generated_at: datetime = None

    def __post_init__(self):
        if self.generated_at is None:
            self.generated_at = datetime.now(timezone.utc)


class ExitManager:
    """
    Evaluates exit conditions and generates exit orders.

    Called on every M1 bar for all active positions.
    """

    def __init__(
        self,
        max_flat_hours: float = 5.0,
        min_move_pct: float = 0.002,
        slippage: float = 0.0005,
    ):
        self._max_flat_minutes = int(max_flat_hours * 60)
        self._min_move_pct = min_move_pct
        self._slippage = slippage

    def evaluate_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        current_price: float,
        position_qty: int,
        trailing_stop: float,
        bars_held: int,
        flat_bars: int,
        highest_price: float,
        stop_breached: bool,
        time_decay_exit: bool,
        exit_reason: str,
        regime_crisis: bool = False,
    ) -> Optional[ExitOrder]:
        """
        Evaluate a single position for exit conditions.

        Returns ExitOrder if a condition triggers, None otherwise.

        Priority:  regime crisis > trailing stop > time decay
        """
        # Crisis: exit everything immediately
        if regime_crisis:
            exit_price = round(current_price * (1 - self._slippage), 2)
            logger.warning(f"CRISIS EXIT: {symbol} @ market ~{exit_price}")
            return ExitOrder(
                symbol=symbol,
                side="SELL" if side == "LONG" else "BUY",
                exit_price=exit_price,
                quantity=position_qty,
                exit_reason="regime crisis — exit all",
                stop_price=trailing_stop,
            )

        # Trailing stop breach
        if stop_breached:
            exit_price = round(trailing_stop * (1 - self._slippage), 2)
            logger.info(
                f"STOP EXIT: {symbol} trail={trailing_stop:.2f} "
                f"(highest={highest_price:.2f}, held={bars_held} bars)"
            )
            return ExitOrder(
                symbol=symbol,
                side="SELL" if side == "LONG" else "BUY",
                exit_price=exit_price,
                quantity=position_qty,
                exit_reason=exit_reason or "trailing stop breached",
                stop_price=trailing_stop,
            )

        # Time decay
        if time_decay_exit:
            exit_price = round(current_price * (1 - self._slippage), 2)
            logger.info(
                f"DECAY EXIT: {symbol} flat for {flat_bars} min "
                f"(move since entry: {(current_price / entry_price - 1):.3%})"
            )
            return ExitOrder(
                symbol=symbol,
                side="SELL" if side == "LONG" else "BUY",
                exit_price=exit_price,
                quantity=position_qty,
                exit_reason=exit_reason or "time decay",
                stop_price=trailing_stop,
            )

        return None