"""
Universe manager — merges seed universe with screener output.

Phase A: Use a static seed list (top 50 S&P 500 liquid stocks)
Phase B: Merge with watchlist screener output for dynamic expansion
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class UniverseManager:
    """
    Manages the candidate stock universe for the trading engine.

    Combines:
      - Seed universe: hardcoded list of top S&P 500 liquid stocks (configs/seed_universe.json)
      - Screener output: daily watchlist from the existing screener pipeline
    """

    def __init__(
        self,
        seed_tickers: list[str],
        use_screener: bool = False,
        screener_path: str = "",
    ):
        self._seed = list(seed_tickers)
        self._use_screener = use_screener
        self._screener_path = Path(screener_path) if screener_path else None
        self._cached: list[str] = []
        self._approved: list[str] = []  # after Layer 1 SMA filter

    def get_candidates(self) -> list[str]:
        """
        Return the full candidate universe before Layer 1 filtering.

        If screener integration is enabled, merges seed + screener output.
        Otherwise returns seed list only.
        """
        candidates = list(self._seed)

        if self._use_screener and self._screener_path:
            screener_symbols = self._load_screener()
            for sym in screener_symbols:
                if sym not in candidates:
                    candidates.append(sym)
            logger.info(
                f"Universe: {len(candidates)} candidates "
                f"({len(self._seed)} seed + {len(screener_symbols)} screener)"
            )

        self._cached = candidates
        return candidates

    def set_approved(self, symbols: list[str]):
        """Set the post-Layer-1 approved shortlist."""
        self._approved = list(symbols)

    @property
    def approved(self) -> list[str]:
        return list(self._approved)

    @property
    def seed(self) -> list[str]:
        return list(self._seed)

    def _load_screener(self) -> list[str]:
        """
        Load tickers from the existing watchlist screener JSON output.

        Expected format (from screener.py):
        {
            "us_market": {
                "top_stocks": [{"symbol": "AAPL", ...}, ...]
            }
        }
        """
        if not self._screener_path or not self._screener_path.exists():
            logger.debug(f"Screener file not found: {self._screener_path}")
            return []

        try:
            with open(self._screener_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read screener file: {e}")
            return []

        symbols = set()
        # Try the expected screener format
        us_data = data.get("us_market", {})
        for stock in us_data.get("top_stocks", []):
            sym = stock.get("symbol", "")
            if sym:
                # Ensure .US suffix
                if not sym.endswith(".US"):
                    sym = f"{sym}.US"
                symbols.add(sym)

        # Also check for the alternate box format
        for box_key in ["long_candidates", "short_candidates"]:
            for stock in us_data.get(box_key, []):
                sym = stock.get("symbol", "")
                if sym:
                    if not sym.endswith(".US"):
                        sym = f"{sym}.US"
                    symbols.add(sym)

        logger.debug(f"Loaded {len(symbols)} tickers from screener")
        return sorted(symbols)