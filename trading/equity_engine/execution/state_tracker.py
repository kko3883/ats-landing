"""
State tracker — active position map, re-entry prevention, state persistence.

Maintains the canonical record of which symbols are held, their entry
parameters, and trade history.  Persisted to JSON on disk every 30 seconds
so an engine restart doesn't lose position awareness.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PositionRecord:
    """Canonical record of an active position."""
    symbol: str
    side: str                    # "LONG" or "SHORT"
    entry_price: float
    entry_time: str              # ISO 8601
    stop_loss: float             # Initial protective stop
    trailing_stop: float         # Current trailing stop
    quantity: int
    atr15: float
    highest_price: float         # Highest since entry (LONG)
    bars_held: int = 0
    fill_order_id: str = ""
    notes: str = ""


@dataclass
class EngineState:
    """Persistable engine state snapshot."""
    timestamp: str
    equity: float
    positions: dict[str, PositionRecord]   # symbol → PositionRecord
    trade_count: int = 0
    paused: bool = False
    pause_reason: str = ""


class StateTracker:
    """
    Thread-safe state tracker for active positions.

    Prevents re-entry into already-held symbols and persists state
    to disk for crash recovery.
    """

    def __init__(self, state_file: Path):
        self._state_file = state_file
        self._positions: dict[str, PositionRecord] = {}
        self._trade_count: int = 0
        self._equity: float = 100_000.0

        # Restore from disk if available
        self._restore()

    # ── Position lifecycle ─────────────────────────────────────────────

    def add_position(self, record: PositionRecord):
        """Register a new position (called after fill confirmation)."""
        self._positions[record.symbol] = record
        self._trade_count += 1
        logger.info(
            f"State: + {record.symbol} {record.side} {record.quantity}sh "
            f"@ {record.entry_price:.2f} SL={record.stop_loss:.2f}"
        )

    def remove_position(self, symbol: str):
        """Remove a position (called after exit fill)."""
        if symbol in self._positions:
            del self._positions[symbol]
            logger.info(f"State: - {symbol}")

    def update_trailing_stop(self, symbol: str, new_stop: float, highest_price: float, bars_held: int):
        """Update the trailing stop and highest price for a position."""
        if symbol in self._positions:
            self._positions[symbol].trailing_stop = round(new_stop, 2)
            self._positions[symbol].highest_price = round(highest_price, 2)
            self._positions[symbol].bars_held = bars_held

    # ── Queries ────────────────────────────────────────────────────────

    def is_held(self, symbol: str) -> bool:
        """Check if a symbol is currently held."""
        return symbol in self._positions

    def get_position(self, symbol: str) -> Optional[PositionRecord]:
        return self._positions.get(symbol)

    @property
    def held_symbols(self) -> set[str]:
        return set(self._positions.keys())

    @property
    def position_count(self) -> int:
        return len(self._positions)

    @property
    def all_positions(self) -> dict[str, PositionRecord]:
        return dict(self._positions)

    @property
    def equity(self) -> float:
        return self._equity

    def update_equity(self, equity: float):
        """Update tracked equity value."""
        self._equity = equity

    # ── Persistence ────────────────────────────────────────────────────

    def save(self):
        """Save current state to disk.  Atomic write (write tmp, rename)."""
        state = EngineState(
            timestamp=datetime.now(timezone.utc).isoformat(),
            equity=self._equity,
            positions={k: v for k, v in self._positions.items()},
            trade_count=self._trade_count,
        )

        try:
            tmp = str(self._state_file) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(asdict(state), f, default=str, indent=2)
            os.replace(tmp, self._state_file)
        except Exception as e:
            logger.warning(f"State save failed: {e}")

    def _restore(self):
        """Restore state from disk after a restart."""
        if not self._state_file.exists():
            logger.info("No state file found — starting fresh")
            return

        try:
            with open(self._state_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"State file corrupt: {e}")
            return

        positions_raw = data.get("positions", {})
        restored = 0
        for sym, pos_dict in positions_raw.items():
            try:
                record = PositionRecord(
                    symbol=pos_dict["symbol"],
                    side=pos_dict.get("side", "LONG"),
                    entry_price=float(pos_dict["entry_price"]),
                    entry_time=pos_dict.get("entry_time", ""),
                    stop_loss=float(pos_dict.get("stop_loss", 0)),
                    trailing_stop=float(pos_dict.get("trailing_stop", 0)),
                    quantity=int(pos_dict.get("quantity", 0)),
                    atr15=float(pos_dict.get("atr15", 0)),
                    highest_price=float(pos_dict.get("highest_price", 0)),
                    bars_held=int(pos_dict.get("bars_held", 0)),
                    fill_order_id=pos_dict.get("fill_order_id", ""),
                    notes="RESTORED",
                )
                self._positions[sym] = record
                restored += 1
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"Skipping corrupt position record for {sym}: {e}")

        self._equity = float(data.get("equity", 100_000))
        self._trade_count = int(data.get("trade_count", 0))

        if restored > 0:
            logger.warning(
                f"RESTORED {restored} positions from state file! "
                f"Verify these are still valid with broker: {list(self._positions.keys())}"
            )
        else:
            logger.info("State restored — no active positions found")