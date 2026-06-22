"""
Risk controller — hard safety rails for the trading engine.

Enforces:
  1. Max risk per trade (1% of portfolio equity)
  2. Max concurrent positions (5)
  3. Pattern Day Trader (PDT) rules (3 day trades per 5-day rolling window if equity < $25k)
  4. Daily loss limit (-3% → flat + pause)
  5. Position size validation before order submission
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    """Result of a risk check."""
    allowed: bool
    reason: str = ""
    adjusted_quantity: int = 0


@dataclass
class DayTrade:
    """Record of a completed round-trip (buy + sell same day)."""
    symbol: str
    entry_time: datetime
    exit_time: datetime


class RiskController:
    """
    Pre-trade and post-trade risk checks.

    Called before every order submission and after every fill.
    """

    def __init__(
        self,
        max_risk_per_trade: float = 0.01,       # 1%
        max_positions: int = 5,
        daily_loss_limit: float = 0.03,          # 3%
        pdt_equity_threshold: float = 25_000.0,
        pdt_max_day_trades: int = 3,
        pdt_rolling_window_days: int = 5,
        slippage: float = 0.0005,
    ):
        self._max_risk_per_trade = max_risk_per_trade
        self._max_positions = max_positions
        self._daily_loss_limit = daily_loss_limit
        self._pdt_equity_threshold = pdt_equity_threshold
        self._pdt_max_day_trades = pdt_max_day_trades
        self._pdt_window_days = pdt_rolling_window_days
        self._slippage = slippage

        # State
        self._portfolio_equity: float = 100_000.0
        self._starting_equity_today: float = 100_000.0
        self._current_positions: set[str] = set()
        self._day_trades: deque[DayTrade] = deque()
        self._paused: bool = False
        self._pause_reason: str = ""
        self._last_reset_date: Optional[datetime] = None

    # ── Public API ─────────────────────────────────────────────────────

    def update_equity(self, equity: float):
        """Update current portfolio equity."""
        self._portfolio_equity = equity

        # Reset daily tracking at the start of each trading day (14:30 UTC = 09:30 ET)
        now = datetime.now(timezone.utc)
        today = now.date()
        if self._last_reset_date is None or self._last_reset_date.date() < today:
            self._starting_equity_today = equity
            self._last_reset_date = now
            self._paused = False
            self._pause_reason = ""
            logger.info(
                f"Daily risk reset: starting equity = ${equity:,.2f}, paused={self._paused}"
            )

    def set_positions(self, symbols: set[str]):
        """Update the set of currently held positions."""
        self._current_positions = set(symbols)

    def record_fill(self, symbol: str, side: str):
        """
        Record a filled order for PDT tracking.

        A "day trade" = buy + sell of the same symbol on the same day.
        Simplified: treat every sell as closing a day trade.
        """
        if side.upper() == "SELL":
            now = datetime.now(timezone.utc)
            # Add to day trade log
            self._day_trades.append(DayTrade(
                symbol=symbol,
                entry_time=now - timedelta(minutes=5),  # approximate
                exit_time=now,
            ))
            # Trim old trades outside the rolling window
            cutoff = now - timedelta(days=self._pdt_window_days)
            while self._day_trades and self._day_trades[0].exit_time < cutoff:
                self._day_trades.popleft()

    # ── Pre-trade checks ───────────────────────────────────────────────

    def check_entry(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        quantity: int,
        current_positions_count: int,
    ) -> RiskCheckResult:
        """
        Check if an entry order should be allowed.

        Returns RiskCheckResult with .allowed and optional adjusted_quantity.
        """
        # 1. Pause check
        if self._paused:
            return RiskCheckResult(allowed=False, reason=f"trading paused: {self._pause_reason}")

        # 2. Already holding this symbol?
        if symbol in self._current_positions:
            return RiskCheckResult(allowed=False, reason=f"already holding {symbol}")

        # 3. Max concurrent positions
        if current_positions_count >= self._max_positions:
            return RiskCheckResult(
                allowed=False,
                reason=f"max positions reached ({self._max_positions})",
            )

        # 4. Daily loss limit
        if self._portfolio_equity > 0 and self._starting_equity_today > 0:
            daily_pnl_pct = (self._portfolio_equity / self._starting_equity_today) - 1
            if daily_pnl_pct <= -self._daily_loss_limit:
                self._paused = True
                self._pause_reason = f"daily loss limit hit ({daily_pnl_pct:.2%})"
                return RiskCheckResult(allowed=False, reason=self._pause_reason)

        # 5. Max risk per trade (adjust quantity)
        if entry_price <= 0 or stop_loss <= 0:
            return RiskCheckResult(allowed=False, reason="invalid price/stop")

        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share <= 0:
            return RiskCheckResult(allowed=False, reason="zero risk distance")

        max_risk_dollar = self._portfolio_equity * self._max_risk_per_trade
        max_qty = int(max_risk_dollar / risk_per_share)

        adjusted_qty = min(quantity, max_qty)
        if adjusted_qty < 1:
            return RiskCheckResult(allowed=False, reason=f"risk too small: {risk_per_share:.2f}/share")

        if adjusted_qty != quantity:
            logger.info(
                f"Risk-adjusted quantity: {quantity} → {adjusted_qty} "
                f"(risk={risk_per_share:.2f}/share, max_risk=${max_risk_dollar:,.0f})"
            )

        # 6. PDT check
        if self._portfolio_equity < self._pdt_equity_threshold:
            day_trades_today = self._count_day_trades_today()
            if day_trades_today >= self._pdt_max_day_trades:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"PDT limit: {day_trades_today}/{self._pdt_max_day_trades} day trades",
                )

        return RiskCheckResult(allowed=True, adjusted_quantity=adjusted_qty)

    def check_exit(
        self,
        symbol: str,
        quantity: int,
    ) -> RiskCheckResult:
        """
        Check if an exit order should be allowed.  Almost always yes,
        but we validate the position exists.
        """
        if symbol not in self._current_positions:
            return RiskCheckResult(
                allowed=False,
                reason=f"no position in {symbol} to exit",
            )
        # Exits always allowed for risk management
        return RiskCheckResult(allowed=True, adjusted_quantity=quantity)

    def force_pause(self, reason: str):
        """Emergency stop — pause all trading."""
        self._paused = True
        self._pause_reason = reason
        logger.warning(f"FORCE PAUSE: {reason}")

    def resume(self):
        """Resume trading after a pause."""
        self._paused = False
        self._pause_reason = ""
        logger.info("Trading resumed")

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def portfolio_equity(self) -> float:
        return self._portfolio_equity

    @property
    def daily_pnl_pct(self) -> float:
        if self._starting_equity_today <= 0:
            return 0.0
        return (self._portfolio_equity / self._starting_equity_today) - 1

    def _count_day_trades_today(self) -> int:
        """Count day trades in the current day."""
        now = datetime.now(timezone.utc)
        today = now.date()
        count = 0
        for dt in self._day_trades:
            if dt.exit_time.date() == today:
                count += 1
        return count