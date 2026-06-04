"""
Signal Engine — Generic strategy evaluator.

Reads strategy definitions from config_strategies.yaml, evaluates them
against the current watchlist, and produces entry/exit signals.

Core design:
  - Strategies are defined IN CONFIG, not in code. Add/modify/remove by editing YAML.
  - Indicator functions are standalone Python functions in indicators.py.
    Add new ones by writing ~10 lines of Python.
  - The engine itself never changes — it just reads config and calls indicators.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from .data_source import fetch_price_data
from .factors import _get_close_panel
from .config import OUTPUT_DIR as WATCHLIST_DIR, MARKETS

# Where strategies are defined
STRATEGIES_CONFIG = Path(__file__).parent / "config_strategies.yaml"
SIGNALS_FILE = Path.home() / ".hermes" / "trading" / "signals.json"
CACHE_DIR = Path.home() / ".hermes" / "trading" / ".cache"


def load_strategies() -> dict:
    """Load all strategy definitions from YAML config."""
    with open(STRATEGIES_CONFIG) as f:
        return yaml.safe_load(f)


def load_watchlist() -> dict:
    """Load latest watchlist output."""
    wl_file = WATCHLIST_DIR / "watchlist.json"
    if not wl_file.exists():
        return None
    with open(wl_file) as f:
        return json.load(f)


def get_state() -> dict:
    """Load current portfolio state from trade journal."""
    state_file = CACHE_DIR / "portfolio_state.json"
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {"positions": []}


def save_signals(signals: dict):
    """Write signals to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / "signals.json"
    path.write_text(json.dumps(signals, indent=2, default=str))
    print(f"  Signals written to {path}")


def evaluate_strategies(watchlist: dict, market: str = "us") -> list[dict]:
    """
    Evaluate all strategies against the current watchlist.

    Each strategy produces a list of signals. A signal = {
        strategy_name, symbol, direction (enter_long/exit_long),
        bucket, meta info
    }
    """
    from .strategies.registry import evaluate_strategy

    strategies = load_strategies()
    signals = []

    for strat_name, strat_cfg in strategies.get("strategies", {}).items():
        if not strat_cfg.get("enabled", True):
            continue

        # Check if this strategy applies to this market
        target_markets = strat_cfg.get("markets", ["us"])
        if market not in target_markets:
            continue

        # Get the watchlist groups this strategy targets
        target_groups = strat_cfg.get("watchlist_groups", [])
        candidate_symbols = []
        for gname in target_groups:
            group_data = watchlist.get("groups", {}).get(gname, {})
            for s in group_data.get("long_candidates", []):
                candidate_symbols.append((s["symbol"], "long", gname))
            for s in group_data.get("short_candidates", []):
                candidate_symbols.append((s["symbol"], "short", gname))

        if not candidate_symbols:
            continue

        # Evaluate entry/exit conditions for each candidate
        strat_signals = evaluate_strategy(
            strat_name, strat_cfg, candidate_symbols, market
        )
        signals.extend(strat_signals)

    return signals


def run(market: str = "us"):
    """Main entry point: load data, evaluate, output signals."""
    print(f"  Loading watchlist...")
    wl = load_watchlist()
    if wl is None:
        print("  No watchlist found. Run screener first.")
        return

    market_data = wl.get(market)
    if not market_data or "groups" not in market_data:
        print(f"  No market data for {market}.")
        return

    print(f"  Evaluating strategies against {market.upper()} watchlist...")
    signals = evaluate_strategies(market_data, market)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market": market,
        "total_signals": len(signals),
        "signals": signals,
    }

    save_signals(result)

    # Publish signals to Supabase
    _publish_to_supabase(signals)

    # Print summary
    entries = [s for s in signals if s.get("action") == "enter_long"]
    exits = [s for s in signals if s.get("action") == "exit_long"]
    print(f"  Entry signals: {len(entries)}")
    for s in entries[:10]:
        strat = s.get("strategy_name", "?")
        sym = s.get("symbol", "?")
        print(f"    {strat:30s} → LONG {sym}")
    print(f"  Exit signals: {len(exits)}")
    for s in exits[:5]:
        sym = s.get("symbol", "?")
        print(f"     EXIT {sym}")


def _publish_to_supabase(signals: list[dict]):
    """Publish signals to Supabase (best-effort, non-blocking)."""
    try:
        from supabase_writer import publish_signals

        publish_signals(signals)
    except ImportError:
        print("  (supabase_writer not available — skipping Supabase publish)")
    except Exception as e:
        print(f"  (Supabase publish failed: {e})")


if __name__ == "__main__":
    run()
