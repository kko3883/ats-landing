"""
4-factor scoring engine.

Each factor is computed independently and z-scored, then combined using
regime-adaptive weights from config.py.

Factors:
  - Momentum (UMD):     6-month total return minus most recent month
                          Skip 1 month to avoid short-term reversal (Jegadeesh & Titman)
  - Quality:             Composite of ROE, gross margin, debt/equity z-scores
  - Low Volatility:      Inverse of 6-month daily return std dev
  - Value:               FCF yield (or dividend yield as fallback)

For HK: quality is disabled (yfinance data unreliable), value uses dividend yield.
"""

import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)


def _get_close_panel(prices: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """
    Extract a clean (Date × Symbol) panel of adjusted close prices.

    yfinance.download returns a MultiIndex column structure:
      Columns: (AAPL, Open), (AAPL, Close), (MSFT, Open), ...
    or a wide DataFrame with single-level columns like ('Close', 'AAPL').

    We need to handle both shapes.
    """
    if prices.empty:
        return pd.DataFrame()

    # Case 1: MultiIndex columns — typical yfinance.download output
    if isinstance(prices.columns, pd.MultiIndex):
        # Two possible orderings from yfinance:
        #   Order A: ('Ticker', 'Price')  -- names=['Ticker', 'Price']
        #   Order B: ('Price', 'Ticker')   -- names=['Price', 'Ticker']
        # Also, auto_adjust=True adds 'Adj Close' alongside 'Close'.
        level_names = prices.columns.names  # ['Ticker', 'Price'] or ['Price', 'Ticker']

        # Determine which level holds the price fields
        if level_names[0] == "Price" or level_names[0] == "price":
            ticker_axis, price_axis = 1, 0  # Order B: xs('Close', level=0)
        elif level_names[1] == "Price" or level_names[1] == "price":
            ticker_axis, price_axis = 0, 1  # Order A: xs('Close', level='Price')
        else:
            # Fallback: try to figure it out from values
            level0_vals = set(prices.columns.get_level_values(0))
            if {"Open", "High", "Low", "Close", "Volume"}.intersection(level0_vals):
                ticker_axis, price_axis = 1, 0  # Level 0 = Price fields
            else:
                ticker_axis, price_axis = 0, 1  # Level 0 = Tickers

        # Extract Close prices
        try:
            if price_axis == 0:
                close = prices.xs("Close", axis=1, level=0)
            else:
                close = prices.xs("Close", axis=1, level=1)
            if close.empty:
                # Fallback: try 'Adj Close'
                if price_axis == 0:
                    close = prices.xs("Adj Close", axis=1, level=0)
                else:
                    close = prices.xs("Adj Close", axis=1, level=1)
        except KeyError:
            return pd.DataFrame()
    else:
        # Case 2: Single-level columns (less common)
        close = prices

    # Filter to requested symbols (only those present)
    available = [s for s in symbols if s in close.columns]
    result = close[available]

    # Deduplicate columns — yfinance can return duplicate columns for some tickers
    # (e.g., 'ECL' appearing twice). Keep the first occurrence.
    if result.columns.duplicated().any():
        result = result.loc[:, ~result.columns.duplicated(keep="first")]

    return result.dropna(how="all")


def _compute_vol_panel(prices: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """
    Extract a (Date × Symbol) panel of daily returns (for vol calculation).
    Same shape handling as _get_close_panel.
    """
    close = _get_close_panel(prices, symbols)
    if close.empty:
        return pd.DataFrame()
    return close.pct_change().dropna(how="all")


def _compute_volume_panel(prices: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """
    Extract a (Date × Symbol) panel of daily volumes (for Tier 3 filters).

    Same column handling as _get_close_panel but extracts 'Volume' instead of 'Close'.
    """
    if prices.empty:
        return pd.DataFrame()

    if isinstance(prices.columns, pd.MultiIndex):
        level_names = prices.columns.names
        if level_names[0] == "Price" or level_names[0] == "price":
            price_axis = 0
        elif level_names[1] == "Price" or level_names[1] == "price":
            price_axis = 1
        else:
            level0_vals = set(prices.columns.get_level_values(0))
            if {"Open", "High", "Low", "Close", "Volume"}.intersection(level0_vals):
                price_axis = 0
            else:
                price_axis = 1

        try:
            if price_axis == 0:
                vol = prices.xs("Volume", axis=1, level=0)
            else:
                vol = prices.xs("Volume", axis=1, level=1)
        except KeyError:
            return pd.DataFrame()
    else:
        vol = prices

    available = [s for s in symbols if s in vol.columns]
    result = vol[available]
    if result.columns.duplicated().any():
        result = result.loc[:, ~result.columns.duplicated(keep="first")]
    return result


# ── Factor 1: Momentum ──────────────────────────────────────────────────────


def factor_momentum(
    close_panel: pd.DataFrame,
    lookback_months: int = 6,
    skip_months: int = 1,
) -> dict[str, float]:
    """
    6-month momentum, skipping the most recent month.

    Returns {symbol: z_score}.
    """
    if close_panel.empty or len(close_panel) < 60:
        return {}

    # Trading days: ~21 per month
    skip_days = skip_months * 21
    lookback_days = lookback_months * 21

    # Returns from t - lookback to t - skip (skip the most recent month)
    # Use the most recent data point as reference
    end_date = close_panel.index[-1]

    # Skip the most recent 21 days
    if len(close_panel) < skip_days + 5:
        return {}

    start_idx = max(0, len(close_panel) - lookback_days - skip_days)
    end_idx = len(close_panel) - skip_days

    start_price = close_panel.iloc[max(0, end_idx - lookback_days)]
    end_price = close_panel.iloc[end_idx - 1] if end_idx > 0 else close_panel.iloc[-1]

    returns = (end_price / start_price - 1.0).dropna()

    if returns.empty:
        return {}

    # Z-score
    mean_r, std_r = returns.mean(), returns.std()
    if std_r < 1e-8:
        return {sym: 0.0 for sym in returns.index}

    return {sym: (r - mean_r) / std_r for sym, r in returns.items()}


# ── Factor 2: Quality ───────────────────────────────────────────────────────


def factor_quality(info: dict[str, dict]) -> dict[str, float]:
    """
    Composite quality score: z(ROE) + z(Gross Margin) + z(-Debt/Equity).

    ROE = netIncome / totalStockholderEquity
    Gross margin = grossProfit / totalRevenue
    Leverage = -totalDebt / totalStockholderEquity (negative = less debt is better)

    Returns {symbol: z_score}. Returns {} if info is empty.
    """
    if not info:
        return {}

    scores = {}
    for sym, d in info.items():
        try:
            if not isinstance(d, dict):
                continue
            net_income = d.get("netIncomeToCommon", d.get("netIncome"))
            equity = d.get("stockholderEquity", d.get("totalStockholderEquity"))
            gross_profit = d.get("grossProfit")
            revenue = d.get("totalRevenue")
            debt = d.get("totalDebt", d.get("longTermDebt"))

            roe = net_income / equity if (net_income and equity and equity != 0) else None
            gm = gross_profit / revenue if (gross_profit and revenue and revenue != 0) else None
            leverage = -(debt / equity) if (debt and equity and equity != 0) else None

            if roe is None and gm is None and leverage is None:
                continue

            # Build composite (only use available metrics)
            components = []
            if roe is not None:
                components.append(("roe", roe))
            if gm is not None:
                components.append(("gm", gm))
            if leverage is not None:
                components.append(("leverage", leverage))

            if not components:
                continue

            scores[sym] = components
        except (TypeError, ZeroDivisionError):
            continue

    if not scores:
        return {}

    # Z-score each component, then average
    all_roe = np.array([c[0][1] for c in scores.values() if c[0][0] == "roe"])
    all_gm = np.array([c[0][1] for c in scores.values() if c[0][0] == "gm"])
    all_lev = np.array([c[0][1] for c in scores.values() if c[0][0] == "leverage"])

    def zscore(arr):
        if len(arr) < 3:
            return {}
        m, s = np.mean(arr), np.std(arr)
        if s < 1e-8:
            return {}
        return {sym: float((val - m) / s) for sym, val in arr}

    roe_z = zscore([(sym, v) for sym, comps in scores.items() for name, v in comps if name == "roe"])
    gm_z = zscore([(sym, v) for sym, comps in scores.items() for name, v in comps if name == "gm"])
    lev_z = zscore([(sym, v) for sym, comps in scores.items() for name, v in comps if name == "leverage"])

    result = {}
    all_symbols = set(roe_z) | set(gm_z) | set(lev_z)
    for sym in all_symbols:
        vals = [roe_z.get(sym, 0), gm_z.get(sym, 0), lev_z.get(sym, 0)]
        result[sym] = np.mean(vals)

    return result


# ── Factor 3: Low Volatility ────────────────────────────────────────────────


def factor_low_vol(
    returns_panel: pd.DataFrame,
) -> dict[str, float]:
    """
    Inverse of 6-month daily return standard deviation.

    Returns {symbol: z_score}. Higher score = more stable = preferred.
    """
    if returns_panel.empty or len(returns_panel) < 20:
        return {}

    vol = returns_panel.std().dropna()

    if vol.empty:
        return {}

    # Invert: lower vol = higher score
    inv_vol = 1.0 / vol.clip(lower=1e-6)

    mean_v, std_v = inv_vol.mean(), inv_vol.std()
    if std_v < 1e-8:
        return {sym: 0.0 for sym in inv_vol.index}

    return {sym: float((v - mean_v) / std_v) for sym, v in inv_vol.items()}


# ── Factor 4: Value ─────────────────────────────────────────────────────────


def factor_value(
    info: dict[str, dict],
    price_panel: pd.DataFrame,
) -> dict[str, float]:
    """
    Value score: FCF yield (FCF / Market Cap) if available, else dividend yield.

    For HK stocks where FCF data may not be available, falls back to
    dividendYield or trailingPE (inverse).

    Returns {symbol: z_score}.
    """
    if not info:
        return {}

    values = {}
    for sym, d in info.items():
        try:
            # Primary: FCF yield
            fcf = d.get("freeCashflow")
            mkt_cap = d.get("marketCap")
            if fcf is not None and mkt_cap and mkt_cap > 0:
                fcf_yield = fcf / mkt_cap
                values[sym] = fcf_yield
                continue

            # Fallback 1: Dividend yield
            div_yield = d.get("dividendYield")
            if div_yield is not None and div_yield > 0:
                values[sym] = div_yield
                continue

            # Fallback 2: Inverse P/E (earnings yield)
            pe = d.get("trailingPE")
            if pe is not None and pe > 0:
                values[sym] = 1.0 / pe
                continue

        except (TypeError, ZeroDivisionError):
            continue

    if not values:
        return {}

    arr = np.array(list(values.values()))
    mean_v, std_v = np.mean(arr), np.std(arr)
    if std_v < 1e-8:
        return {sym: 0.0 for sym in values}

    return {sym: float((v - mean_v) / std_v) for sym, v in values.items()}


# ── Factor 4: Value (Price Proxy) ──────────────────────────────────────────


def factor_value_proxy(close_panel: pd.DataFrame) -> dict[str, float]:
    """
    Simple price-based value proxy: 6-month price decline = potentially oversold.
    This is NOT a true value factor (replaces FCF yield when info data unavailable).

    Higher score = more decline = potential value opportunity.
    Used when info-based value factor (FCF yield) is unavailable.

    Returns {symbol: z_score}.
    """
    if close_panel.empty or len(close_panel) < 60:
        return {}

    # 6-month return
    start = close_panel.iloc[0]
    end = close_panel.iloc[-1]
    returns = (end / start - 1.0).dropna()

    # Invert: more decline = higher score
    value_scores = -returns

    mean_v, std_v = value_scores.mean(), value_scores.std()
    if std_v < 1e-8:
        return {sym: 0.0 for sym in value_scores.index}

    return {sym: float((v - mean_v) / std_v) for sym, v in value_scores.items()}


# ── Price-Only Scoring Pipeline (No Info Data Needed) ─────────────────────


def score_stocks_price_only(
    prices: pd.DataFrame,
    symbols: list[str],
    factor_weights: dict[str, float],
) -> pd.DataFrame:
    """
    Compute factor scores using ONLY price data (no financial info needed).

    Uses: momentum, low_vol, value_proxy.
    Skips: quality (requires financial info).

    Returns a DataFrame indexed by symbol with columns:
      - momentum, low_vol, value_proxy (individual z-scores)
      - score (weighted composite)
    """
    close = _get_close_panel(prices, symbols)
    returns = _compute_vol_panel(prices, symbols)

    scores = {}

    if factor_weights.get("momentum", 0) > 0:
        mom = factor_momentum(close)
        scores["momentum"] = mom
        print(f"    Momentum: {len(mom)} symbols scored")

    if factor_weights.get("quality", 0) > 0:
        # Quality explicitly skipped for price-only mode
        print(f"    Quality: skipped (requires financial info)")

    if factor_weights.get("low_vol", 0) > 0:
        lv = factor_low_vol(returns)
        scores["low_vol"] = lv
        print(f"    Low Vol: {len(lv)} symbols scored")

    if factor_weights.get("value", 0) > 0:
        val = factor_value_proxy(close)
        scores["value"] = val
        print(f"    Value (proxy): {len(val)} symbols scored")

    # Build composite
    all_symbols = set()
    for factor_scores in scores.values():
        all_symbols.update(factor_scores.keys())

    # Redistribute quality weight if it was set but not computed
    effective_weights = dict(factor_weights)
    if "quality" in effective_weights and effective_weights["quality"] > 0:
        skip = effective_weights.pop("quality", 0)
        # Redistribute proportionally to remaining factors
        remaining = sum(effective_weights.values())
        if remaining > 0:
            for k in effective_weights:
                effective_weights[k] += skip * (effective_weights[k] / remaining)

    rows = []
    for sym in sorted(all_symbols):
        row = {"symbol": sym}
        composite = 0.0
        for factor_name, weight in effective_weights.items():
            if weight == 0:
                continue
            z = scores.get(factor_name, {}).get(sym, 0.0)
            row[factor_name] = round(z, 3)
            composite += weight * z
        row["score"] = round(composite, 3)
        rows.append(row)

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values("score", ascending=False).reset_index(drop=True)

    return result


def score_stocks(
    prices: pd.DataFrame,
    info: dict[str, dict],
    symbols: list[str],
    factor_weights: dict[str, float],
    market: str,
) -> pd.DataFrame:
    """
    Compute 4-factor composite scores for all symbols.

    Returns a DataFrame indexed by symbol with columns:
      - momentum, quality, low_vol, value (individual z-scores)
      - score (weighted composite)
    """
    close = _get_close_panel(prices, symbols)
    returns = _compute_vol_panel(prices, symbols)

    scores = {}

    if factor_weights.get("momentum", 0) > 0:
        scores["momentum"] = factor_momentum(close)
        print(f"    Momentum: {len(scores['momentum'])} symbols scored")

    if factor_weights.get("quality", 0) > 0:
        scores["quality"] = factor_quality(info)
        print(f"    Quality: {len(scores['quality'])} symbols scored")

    if factor_weights.get("low_vol", 0) > 0:
        scores["low_vol"] = factor_low_vol(returns)
        print(f"    Low Vol: {len(scores['low_vol'])} symbols scored")

    if factor_weights.get("value", 0) > 0:
        scores["value"] = factor_value(info, close)
        print(f"    Value: {len(scores['value'])} symbols scored")

    # Build DataFrame
    all_symbols = set()
    for factor_scores in scores.values():
        all_symbols.update(factor_scores.keys())

    rows = []
    for sym in sorted(all_symbols):
        row = {"symbol": sym}
        composite = 0.0
        for factor_name, weight in factor_weights.items():
            if weight == 0:
                continue
            z = scores.get(factor_name, {}).get(sym, 0.0)
            row[factor_name] = round(z, 3)
            composite += weight * z
        row["score"] = round(composite, 3)
        rows.append(row)

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values("score", ascending=False).reset_index(drop=True)

    return result
