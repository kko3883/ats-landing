"""
Strategy registry — maps YAML strategy definitions to Python indicator functions.

The registry is the bridge between config_strategies.yaml and the indicator functions.
To add a new indicator type:
  1. Write the function in indicators.py
  2. Add it to REGISTRY below — that's it
"""

from typing import Any, Callable

import pandas as pd

from . import indicators

# ── Registry: maps YAML indicator names → (function, requires_volume?) ─────

REGISTRY: dict[str, tuple[Callable, bool]] = {
    # Entry/exit conditions
    "rsi_lt": (indicators.rsi_lt, False),
    "rsi_gt": (indicators.rsi_gt, False),
    "price_above_sma": (indicators.price_above_sma, False),
    "price_below_sma": (indicators.price_below_sma, False),
    "near_sma": (indicators.near_sma, False),
    "sma_crossover": (indicators.sma_crossover, False),
    "sma_crossunder": (indicators.sma_crossunder, False),
    "breakout": (indicators.breakout, True),
    "pullback": (indicators.pullback, True),
    "bb_squeeze": (indicators.bb_squeeze, True),

    # Displays (just dump context, not boolean)
    "rsi": (lambda close, **kw: indicators.rsi(close, kw.get("period", 14)).iloc[-1], False),
    "sma": (lambda close, **kw: indicators.sma(close, kw.get("period", 50)).iloc[-1], False),
    "atr": (None, True),  # needs special treatment (high+low)
}

# ── Condition Evaluator ────────────────────────────────────────────────────


def evaluate_conditions(
    conditions: list[dict],
    close: "pd.Series",
    volume: "pd.Series | None" = None,
) -> tuple[bool, dict]:
    """
    Evaluate a list of conditions (AND logic — ALL must be true).

    Each condition:
      {indicator: "rsi_lt", period: 14, threshold: 35}
      → calls rsi_lt(close, period=14, threshold=35) → returns bool

    Returns (all_passed, context_dict).
    """
    context = {}
    for cond in conditions:
        indicator = cond.get("indicator")
        if indicator not in REGISTRY:
            print(f"    Unknown indicator: {indicator}")
            return False, context

        fn, needs_vol = REGISTRY[indicator]
        if fn is None:
            # Special case — skip evaluation, add to context
            continue

        # Build kwargs from condition (skip 'indicator' field)
        kwargs = {k: v for k, v in cond.items() if k != "indicator"}

        try:
            if needs_vol:
                if volume is None:
                    return False, context
                result = fn(close, volume=volume, **kwargs)
            else:
                result = fn(close, **kwargs)

            # Store in context
            context[indicator] = result

            # Boolean check — only if result IS a boolean
            if isinstance(result, bool):
                if not result:
                    return False, context
            # String results (like divergence) are informational
        except Exception as e:
            print(f"    Error evaluating {indicator}: {e}")
            return False, context

    return True, context


# ── Strategy Evaluator ─────────────────────────────────────────────────────


def evaluate_strategy(
    strat_name: str,
    strat_cfg: dict,
    candidates: list[tuple[str, str, str]],
    market: str,
) -> list[dict]:
    """
    Evaluate entry/exit conditions for a strategy against candidates.

    Each candidate is (symbol, direction, group_name).
    Returns list of signal dicts.
    """
    from ..data_source import fetch_price_data
    from ..config import MARKETS
    from ..macro_betas import _get_close_panel

    signals = []

    # Fetch price data for evaluation
    conf = MARKETS.get(market, {})
    universe_file = conf.get("universe_file")
    if universe_file is None:
        return signals

    # Load symbols for data fetching
    import json
    with open(universe_file) as f:
        universe_data = json.load(f)
    if market == "us":
        all_symbols = universe_data.get("stocks", [])
    else:
        all_symbols = universe_data.get("all_sectors", [])

    try:
        prices = fetch_price_data(all_symbols, market)
    except Exception as e:
        print(f"  Cannot fetch price data: {e}")
        return signals

    close_panel = _get_close_panel(prices)

    # Extract volume if needed
    volume_panel = None
    needs_volume = any(
        REGISTRY.get(c.get("indicator"), (None, False))[1]
        for c in strat_cfg.get("entry", {}).get("conditions", [])
    ) or any(
        REGISTRY.get(c.get("indicator"), (None, False))[1]
        for c in strat_cfg.get("exit", {}).get("conditions", [])
    )

    if needs_volume:
        if isinstance(prices.columns, pd.MultiIndex):
            level_names = prices.columns.names
            price_axis = 0 if level_names[0] in ("Price", "price") else 1
            try:
                vol = prices.xs("Volume", axis=1, level=price_axis)
                macro_syms = {"^VIX", "DX-Y.NYB", "HYG", "TLT", "^TNX", "^IRX"}
                stock_cols = [c for c in vol.columns if str(c) not in macro_syms]
                vol = vol[stock_cols]
                if vol.columns.duplicated().any():
                    vol = vol.loc[:, ~vol.columns.duplicated(keep="first")]
                volume_panel = vol
            except KeyError:
                pass

    entry_cfg = strat_cfg.get("entry", {})
    exit_cfg = strat_cfg.get("exit", {})
    sizing = strat_cfg.get("sizing", {})

    # Track which symbols we enter (so exits only fire for held positions)
    entered_symbols = set()

    for symbol, direction, group_name in candidates:
        if symbol not in close_panel.columns:
            continue

        close = close_panel[symbol].dropna()
        volume = volume_panel[symbol].dropna() if volume_panel is not None and symbol in volume_panel.columns else None

        if len(close) < 60:
            continue

        # Evaluate ENTRY
        entry_passed, entry_ctx = evaluate_conditions(
            entry_cfg.get("conditions", []), close, volume
        )

        if entry_passed:
            signal = {
                "strategy_name": strat_name,
                "symbol": symbol,
                "action": "enter_long" if direction == "long" else "enter_short",
                "direction": direction,
                "bucket": strat_cfg.get("bucket", "alpha_gen"),
                "group": group_name,
                "price": float(close.iloc[-1]),
                "watchlist_score": None,
                "context": entry_ctx,
                "stop_loss": None,
                "take_profit": None,
            }

            # Add sizing info
            sl_pct = sizing.get("stop_loss_pct") or exit_cfg.get("stop_loss_pct")
            if sl_pct:
                signal["stop_loss"] = round(float(close.iloc[-1]) * (1 - sl_pct), 2)

            tp_pct = sizing.get("take_profit_pct") or exit_cfg.get("take_profit_pct")
            if tp_pct:
                signal["take_profit"] = round(float(close.iloc[-1]) * (1 + tp_pct), 2)

            signals.append(signal)
            entered_symbols.add(symbol)
            continue

        # Evaluate EXIT — ONLY for symbols we entered or have open positions
        if symbol in entered_symbols:
            exit_passed, exit_ctx = evaluate_conditions(
                exit_cfg.get("conditions", []), close, volume
            )
            if exit_passed:
                signals.append({
                    "strategy_name": strat_name,
                    "symbol": symbol,
                    "action": "exit_long" if direction == "long" else "exit_short",
                    "direction": direction,
                    "bucket": strat_cfg.get("bucket", "alpha_gen"),
                    "price": float(close.iloc[-1]),
                    "reason": exit_ctx,
                })

    return signals
