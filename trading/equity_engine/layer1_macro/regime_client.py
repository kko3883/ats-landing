"""
Regime client — reads market regime from Supabase.

The `regime_detector.py` cron job writes the current regime to the
`regime` table.  This module reads the latest row and maps it to
trading mode constraints for the equity engine.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RegimeState:
    """Parsed regime state from Supabase."""
    regime_name: str          # "risk_on", "choppy", "risk_off", "crisis"
    vix_level: float
    description: str
    activated_groups: list[str]
    created_at: datetime
    # Derived constraints
    allow_new_entries: bool = True
    tighten_stops: bool = False
    reduce_sizing: bool = False
    exit_all: bool = False


# Regime → trading constraints
REGIME_CONSTRAINTS = {
    "risk_on": {
        "allow_new_entries": True,
        "tighten_stops": False,
        "reduce_sizing": False,
        "exit_all": False,
    },
    "choppy": {
        "allow_new_entries": True,
        "tighten_stops": True,       # tighter trailing stops
        "reduce_sizing": True,       # half-size positions
        "exit_all": False,
    },
    "risk_off": {
        "allow_new_entries": False,  # no new entries
        "tighten_stops": True,
        "reduce_sizing": False,
        "exit_all": False,           # manage existing, but don't add
    },
    "crisis": {
        "allow_new_entries": False,
        "tighten_stops": True,
        "reduce_sizing": False,
        "exit_all": True,            # liquidate everything
    },
}


def parse_regime_row(row: dict) -> Optional[RegimeState]:
    """
    Parse a Supabase regime row into a RegimeState.
    Returns None if row is invalid.
    """
    if not row:
        return None

    name = row.get("regime_name", "choppy")
    constraints = REGIME_CONSTRAINTS.get(name, REGIME_CONSTRAINTS["choppy"])

    created = row.get("created_at")
    if isinstance(created, str):
        created = datetime.fromisoformat(created.replace("Z", "+00:00"))

    return RegimeState(
        regime_name=name,
        vix_level=float(row.get("vix_level", 20)),
        description=row.get("description", ""),
        activated_groups=row.get("activated_groups", []),
        created_at=created or datetime.now(timezone.utc),
        **constraints,
    )


class RegimeClient:
    """
    Reads the latest market regime from Supabase.

    Can work in two modes:
      1. HTTP: direct Supabase REST API (needs anon key)
      2. Static: uses a default regime (for backtesting or offline)
    """

    def __init__(
        self,
        supabase_url: str = "",
        supabase_anon_key: str = "",
        default_regime: str = "risk_on",
    ):
        self._url = supabase_url
        self._key = supabase_anon_key
        self._default_regime = default_regime
        self._cached: Optional[RegimeState] = None
        self._last_fetch: Optional[datetime] = None

    def fetch_regime(self) -> RegimeState:
        """
        Get the current regime.  Uses cached value if fetched within
        the last 5 minutes.
        """
        now = datetime.now(timezone.utc)
        if self._cached and self._last_fetch:
            age = (now - self._last_fetch).total_seconds()
            if age < 300:  # 5 min cache
                return self._cached

        # Try Supabase HTTP
        if self._url and self._key:
            try:
                regime = self._fetch_from_supabase()
                if regime:
                    self._cached = regime
                    self._last_fetch = now
                    logger.info(
                        f"Regime: {regime.regime_name.upper()} "
                        f"(VIX={regime.vix_level:.1f}, "
                        f"entries={'YES' if regime.allow_new_entries else 'NO'})"
                    )
                    return regime
            except Exception as e:
                logger.warning(f"Supabase regime fetch failed: {e}")

        # Fallback: default regime
        constraints = REGIME_CONSTRAINTS.get(
            self._default_regime,
            REGIME_CONSTRAINTS["choppy"],
        )
        fallback = RegimeState(
            regime_name=self._default_regime,
            vix_level=20.0,
            description="Default (offline/fallback)",
            activated_groups=[],
            created_at=now,
            **constraints,
        )
        logger.info(f"Regime fallback: {fallback.regime_name.upper()}")
        return fallback

    def _fetch_from_supabase(self) -> Optional[RegimeState]:
        """Fetch latest regime row from Supabase REST API."""
        import urllib.request

        url = f"{self._url}/rest/v1/regime?select=*&order=created_at.desc&limit=1"
        req = urllib.request.Request(url)
        req.add_header("apikey", self._key)
        req.add_header("Authorization", f"Bearer {self._key}")
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if data and isinstance(data, list) and len(data) > 0:
                return parse_regime_row(data[0])
        return None