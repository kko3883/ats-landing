"""
Entry manager — generates trading orders when Layer 2 probability exceeds threshold.

Calculates:
  - Entry price (limit at pullback zone or market)
  - Initial protective stop loss: Entry - (2 × ATR_15)
  - Position size: risk-based (1% of equity per trade)
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ..data.longbridge_stream import Bar

logger = logging.getLogger(__name__)


@dataclass
class EntryOrder:
    """A generated entry order ready for execution."""
    symbol: str
    side: str                    # "BUY" or "SELL" (for short-selling)
    entry_price: float           # Limit price or market (use current)
    stop_loss: float             # Initial protective stop
    quantity: int                # Number of shares
    entry_type: str              # "LIMIT" or "MARKET"
    atr: float                   # ATR(15) value used for stop
    prob: float                  # XGBoost probability that triggered this
    generated_at: datetime = None

    def __post_init__(self):
        if self.generated_at is None:
            self.generated_at = datetime.now(timezone.utc)


class EntryManager:
    """
    Generates entry orders when the XGBoost model signals high probability
    of a positive return over the forecast horizon.

    Currently implements LONG-only entries (mean-reversion pullback buys
    above SMA200).  SHORT entries can be added later.
    """

    def __init__(
        self,
        prob_threshold: float = 0.65,
        atr_stop_mult: float = 2.0,
        max_risk_per_trade: float = 0.01,
        portfolio_equity: float = 100_000.0,
        slippage: float = 0.0005,
    ):
        self._prob_threshold = prob_threshold
        self._atr_stop_mult = atr_stop_mult
        self._max_risk_per_trade = max_risk_per_trade
        self._portfolio_equity = portfolio_equity
        self._slippage = slippage

    def update_equity(self, equity: float):
        """Update portfolio equity for position sizing."""
        self._portfolio_equity = equity

    def generate_entry(
        self,
        symbol: str,
        prob: float,
        current_price: float,
        atr: float,
        sma200: float,
        latest_bar: Bar,
    ) -> Optional[EntryOrder]:
        """
        Generate an entry order if probability exceeds threshold.

        Args:
            symbol: Ticker (e.g., "AAPL.US")
            prob: XGBoost entry probability (0.0–1.0)
            current_price: Latest M15 bar close
            atr: ATR(15) value
            sma200: D1 SMA(200) value (Layer 1)
            latest_bar: The current M15 bar

        Returns:
            EntryOrder if entry should be fired, None otherwise
        """
        if prob < self._prob_threshold:
            return None

        if current_price <= 0 or atr <= 0:
            logger.warning(f"{symbol}: invalid price/ATR for entry ({current_price}, {atr})")
            return None

        # LONG only for now — buy at current price (market order)
        side = "BUY"

        # Entry price: use current close (market) or a slight pullback limit
        # For mean-reversion strategy: place limit at current price - 0.5× ATR
        entry_price = round(current_price * (1 - self._slippage), 2)

        # Initial stop loss: Entry - (atr_stop_mult × ATR)
        stop_loss = round(entry_price - (self._atr_stop_mult * atr), 2)

        # Position sizing: risk-based (1% of equity = max loss)
        risk_per_share = entry_price - stop_loss
        if risk_per_share <= 0:
            logger.warning(f"{symbol}: invalid risk per share ({risk_per_share})")
            return None

        max_risk_dollar = self._portfolio_equity * self._max_risk_per_trade
        quantity = int(max_risk_dollar / risk_per_share)
        if quantity < 1:
            logger.debug(f"{symbol}: quantity=0 — entry too small (risk={risk_per_share:.2f})")
            return None

        order = EntryOrder(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            quantity=quantity,
            entry_type="LIMIT",
            atr=atr,
            prob=prob,
        )

        logger.info(
            f"ENTRY SIGNAL: {symbol} {side} {quantity}sh @ {entry_price:.2f} "
            f"(prob={prob:.2%}, SL={stop_loss:.2f}, "
            f"risk={risk_per_share:.2f}/share, "
            f"ATR={atr:.2f})"
        )

        return order


@dataclass
class RejectedEntry:
    """Records why an entry was rejected (for debugging)."""
    symbol: str
    reason: str
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)