"""
Stage 2 & 3: Macro factor betas and cross-sectional relative strength.

Stage 2: For each stock, compute VIX and DXY beta via OLS regression.
         Stock_daily_return = alpha + beta_vix * VIX_change + beta_dxy * DXY_change

Stage 3: Group stocks by VIX beta quintiles, then compute cross-sectional
         relative strength z-scores within each group.

Academic basis:
  - VIX is the market's "fear gauge" — stocks with high negative VIX beta
    (growth/tech) crash when VIX spikes and rally when it drops.
  - DXY captures USD liquidity cycles — a stronger USD hurts multinational
    earners and EM-exposed stocks.
  - Grouping by macro beta before ranking ensures you're comparing apples
    to apples (NVDA vs AMD, not NVDA vs KO).
"""

import numpy as np
import pandas as pd

from .config import VIX_BETA_THRESHOLDS, VIX_BETA_GROUP_LABELS, RS_LOOKBACK_DAYS, RS_TOP_PCT, RS_BOTTOM_PCT


# ── Helpers: Extract Close & Returns ────────────────────────────────────────


def _get_close_panel(prices: pd.DataFrame) -> pd.DataFrame:
    """
    Extract a (Date × Symbol) panel of close prices from yfinance output.
    Handles both column orderings: (Ticker, Price) and (Price, Ticker).

    Filters to only stock symbols (excludes ^VIX, DX-Y.NYB).
    """
    if not isinstance(prices.columns, pd.MultiIndex):
        return prices

    level_names = prices.columns.names
    if level_names[0] in ("Price", "price"):
        price_axis = 0
    elif level_names[1] in ("Price", "price"):
        price_axis = 1
    else:
        return pd.DataFrame()

    try:
        close = prices.xs("Close", axis=1, level=price_axis)
    except KeyError:
        try:
            close = prices.xs("Adj Close", axis=1, level=price_axis)
        except KeyError:
            return pd.DataFrame()

    # Filter out macro symbols
    macro_syms = {"^VIX", "DX-Y.NYB"}
    stock_cols = [c for c in close.columns if str(c) not in macro_syms]
    close = close[stock_cols]

    # Deduplicate
    if close.columns.duplicated().any():
        close = close.loc[:, ~close.columns.duplicated(keep="first")]

    return close.dropna(how="all")


def _daily_returns(close: pd.DataFrame) -> pd.DataFrame:
    """Daily percentage returns from a close price panel."""
    return close.pct_change().dropna(how="all")


# ── Stage 1: Liquidity Screen ────────────────────────────────────────────────


def liquidity_screen(
    prices: pd.DataFrame,
    symbols: list[str],
    min_price: float = 5.0,
    min_adv: float = 20_000_000,
) -> list[str]:
    """
    Filter symbols by price and average daily dollar volume.

    Computes from the price DataFrame directly (no per-ticker info needed).
    """
    close = _get_close_panel(prices)
    if close.empty:
        return []

    # Get volume panel
    if not isinstance(prices.columns, pd.MultiIndex):
        return symbols  # can't determine, pass through

    level_names = prices.columns.names
    if level_names[0] in ("Price", "price"):
        price_axis = 0
    elif level_names[1] in ("Price", "price"):
        price_axis = 1
    else:
        return symbols

    try:
        vol = prices.xs("Volume", axis=1, level=price_axis)
    except KeyError:
        return symbols

    # Filter out macro symbols
    macro_syms = {"^VIX", "DX-Y.NYB"}
    stock_cols = [c for c in vol.columns if str(c) not in macro_syms]
    vol = vol[stock_cols]

    if vol.columns.duplicated().any():
        vol = vol.loc[:, ~vol.columns.duplicated(keep="first")]

    # Compute filters
    passed = []
    for sym in symbols:
        if sym not in close.columns or sym not in vol.columns:
            continue

        c = close[sym].dropna()
        v = vol[sym].dropna()

        if len(c) == 0 or len(v) == 0:
            continue

        latest_price = c.iloc[-1]
        adv = (v.tail(20) * c.tail(20)).mean()  # 20-day avg dollar volume

        if latest_price >= min_price and adv >= min_adv:
            passed.append(sym)

    return list(dict.fromkeys(passed))  # dedup while preserving order


# ── Stage 2: Macro Factor Betas ──────────────────────────────────────────────


def compute_macro_betas(
    stock_returns: pd.DataFrame,
    vix_returns: pd.Series,
    dxy_returns: pd.Series,
) -> dict:
    """
    Compute VIX beta and DXY beta for each stock via OLS regression.

    Model: stock_return = alpha + beta_vix * vix_chg + beta_dxy * dxy_chg

    Returns {symbol: {"beta_vix": float, "beta_dxy": float, "r_squared": float}}.
    """
    # Align all series on common dates
    common_dates = stock_returns.index.intersection(vix_returns.index).intersection(dxy_returns.index)
    stock_returns = stock_returns.loc[common_dates]
    vix_vals = vix_returns.loc[common_dates]
    dxy_vals = dxy_returns.loc[common_dates]

    if len(common_dates) < 30:
        return {}

    vix_chg = vix_vals.values
    dxy_chg = dxy_vals.values

    # Design matrix: [1, vix_chg, dxy_chg]
    X = np.column_stack([np.ones(len(common_dates)), vix_chg, dxy_chg])

    results = {}
    for sym in stock_returns.columns:
        y = stock_returns[sym].values

        # Skip if too much missing data
        valid = ~np.isnan(y)
        if valid.sum() < 30:
            continue

        X_valid = X[valid]
        y_valid = y[valid]

        try:
            beta, residuals, rank, s = np.linalg.lstsq(X_valid, y_valid, rcond=None)
            alpha, beta_vix, beta_dxy = beta

            # R-squared
            y_mean = y_valid.mean()
            ss_total = ((y_valid - y_mean) ** 2).sum()
            ss_residual = (residuals ** 2).sum() if len(residuals) > 0 else 0
            r2 = 1 - ss_residual / ss_total if ss_total > 0 else 0

            results[sym] = {
                "beta_vix": round(float(beta_vix), 4),
                "beta_dxy": round(float(beta_dxy), 4),
                "r_squared": round(float(r2), 4),
            }
        except np.linalg.LinAlgError:
            continue

    return results


# ── Stage 2: Assign to VIX Beta Groups ─────────────────────────────────────


def assign_to_groups(
    betas: dict,
) -> dict:
    """
    Assign each stock to a VIX beta quintile.

    Returns {group_index: {label, stocks: [{symbol, beta_vix, beta_dxy}]}}
    """
    if not betas:
        return {}

    # Sort by VIX beta (ascending — most negative first)
    sorted_stocks = sorted(betas.items(), key=lambda x: x[1]["beta_vix"])

    n = len(sorted_stocks)
    bin_size = max(n // 5, 1)

    groups = {}
    for i, (lo, hi) in enumerate(
        [(0, bin_size), (bin_size, 2 * bin_size), (2 * bin_size, 3 * bin_size),
         (3 * bin_size, 4 * bin_size), (4 * bin_size, n)]
    ):
        label = VIX_BETA_GROUP_LABELS.get(i, f"group_{i}")
        batch = sorted_stocks[lo:hi]
        if not batch:
            continue

        avg_beta_vix = np.mean([b[1]["beta_vix"] for b in batch])
        avg_beta_dxy = np.mean([b[1]["beta_dxy"] for b in batch])

        groups[i] = {
            "label": label,
            "n_stocks": len(batch),
            "avg_beta_vix": round(float(avg_beta_vix), 3),
            "avg_beta_dxy": round(float(avg_beta_dxy), 3),
            "stocks": [
                {
                    "symbol": sym,
                    "beta_vix": betas[sym]["beta_vix"],
                    "beta_dxy": betas[sym]["beta_dxy"],
                    "r_squared": betas[sym]["r_squared"],
                }
                for sym, _ in batch
            ],
        }

    return groups


# ── Stage 3: Cross-Sectional Relative Strength ──────────────────────────────


def cross_sectional_rs(
    groups: dict,
    close_panel: pd.DataFrame,
) -> dict:
    """
    Within each VIX beta group, compute relative strength z-scores.

    RS for each stock = (stock_1m_return - group_median_return) / group_std_return
    Then z-scored within the group.

    Mutates groups in-place, adding 'rs_zscore', 'rs_rank', and 'candidate_type'
    to each stock entry.

    Returns the updated groups dict.
    """
    if close_panel.empty:
        return groups

    for group_idx, group_data in groups.items():
        symbols_in_group = [s["symbol"] for s in group_data["stocks"]]
        if len(symbols_in_group) < 3:
            # Too few stocks — mark all as neutral
            for s in group_data["stocks"]:
                s["rs_zscore"] = 0.0
                s["rs_rank"] = 0
                s["candidate_type"] = "neutral"
            continue

        # Compute 1-month returns
        lookback = min(RS_LOOKBACK_DAYS, len(close_panel))
        if lookback < 5:
            for s in group_data["stocks"]:
                s["rs_zscore"] = 0.0
                s["rs_rank"] = 0
                s["candidate_type"] = "neutral"
            continue

        start_prices = close_panel.iloc[-lookback]
        end_prices = close_panel.iloc[-1]

        returns_1m = {}
        for sym in symbols_in_group:
            if sym in start_prices.index and sym in end_prices.index:
                s = start_prices[sym]
                e = end_prices[sym]
                if pd.notna(s) and pd.notna(e) and s > 0:
                    returns_1m[sym] = float(e / s - 1.0)

        if not returns_1m:
            continue

        values = np.array(list(returns_1m.values()))
        mean_ret = np.mean(values)
        std_ret = np.std(values)

        # Z-score relative to group
        zscores = {
            sym: float((ret - mean_ret) / std_ret) if std_ret > 1e-8 else 0.0
            for sym, ret in returns_1m.items()
        }

        # Rank
        ranked = sorted(zscores.items(), key=lambda x: x[1], reverse=True)
        n = len(ranked)

        # Determine candidate type
        top_n = max(int(n * RS_TOP_PCT), 1)
        bottom_n = max(int(n * RS_BOTTOM_PCT), 1)

        candidate_types = {}
        for rank, (sym, z) in enumerate(ranked):
            if rank < top_n:
                candidate_types[sym] = "long"
            elif rank >= n - bottom_n:
                candidate_types[sym] = "short"
            else:
                candidate_types[sym] = "neutral"

        # Update stock entries
        for s in group_data["stocks"]:
            sym = s["symbol"]
            if sym in zscores:
                s["rs_zscore"] = round(zscores[sym], 3)
                s["candidate_type"] = candidate_types.get(sym, "neutral")

    return groups


# ── Full Pipeline Runner ────────────────────────────────────────────────────


def run_stage2_and_3(
    prices: pd.DataFrame,
    symbols: list[str],
    min_price: float,
    min_adv: float,
) -> dict:
    """
    Run Stages 1–3 of the watchlist pipeline.

    Returns:
    {
        "liquidity_passed": N,
        "total_in_universe": N,
        "groups": {group_index: {label, n_stocks, stocks: [...]}}
    }
    """
    # Stage 1: Liquidity
    passed = liquidity_screen(prices, symbols, min_price, min_adv)
    print(f"  Stage 1 (liquidity): {len(passed)}/{len(symbols)} passed")

    if len(passed) == 0:
        return {
            "liquidity_passed": 0,
            "total_in_universe": len(symbols),
            "groups": {},
        }

    # Extract data
    close = _get_close_panel(prices)
    stock_returns = _daily_returns(close)

    # Extract macro series
    vix_col = None
    dxy_col = None
    if isinstance(prices.columns, pd.MultiIndex):
        level_names = prices.columns.names
        price_axis = 0 if level_names[0] in ("Price", "price") else 1
        try:
            macro_close = prices.xs("Close", axis=1, level=price_axis)
            vix_col = macro_close["^VIX"].dropna() if "^VIX" in macro_close.columns else None
            dxy_col = macro_close["DX-Y.NYB"].dropna() if "DX-Y.NYB" in macro_close.columns else None
        except KeyError:
            pass

    if vix_col is None or dxy_col is None or len(vix_col) < 30:
        print(f"  WARNING: Missing macro data (VIX={vix_col is not None}, DXY={dxy_col is not None})")
        return {
            "liquidity_passed": len(passed),
            "total_in_universe": len(symbols),
            "groups": {},
        }

    vix_returns = vix_col.pct_change().dropna()  # VIX % changes (more meaningful beta magnitudes)
    dxy_returns = dxy_col.pct_change().dropna()  # DXY is a price index, % returns are fine

    # Stage 2: Macro betas
    betas = compute_macro_betas(
        stock_returns[passed], vix_returns, dxy_returns
    )
    print(f"  Stage 2 (betas): {len(betas)} symbols have VIX/DXY betas")

    # Stage 2: Group assignment
    groups = assign_to_groups(betas)
    for gid, gdata in groups.items():
        print(f"    Group {gid} ({gdata['label']}): {gdata['n_stocks']} stocks, "
              f"avg beta_vix={gdata['avg_beta_vix']:+.3f}")

    # Stage 3: Cross-sectional RS
    groups = cross_sectional_rs(groups, close)
    long_count = sum(
        1 for g in groups.values() for s in g["stocks"] if s.get("candidate_type") == "long"
    )
    short_count = sum(
        1 for g in groups.values() for s in g["stocks"] if s.get("candidate_type") == "short"
    )
    print(f"  Stage 3 (RS): {long_count} long + {short_count} short candidates")

    return {
        "liquidity_passed": len(passed),
        "total_in_universe": len(symbols),
        "groups": groups,
    }
