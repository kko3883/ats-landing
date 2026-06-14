"""
Stage 2 & 3: Macro factor betas and cross-sectional relative strength.

Stage 2: For each stock, compute multi-factor betas via OLS regression.
         Stock_daily_return = alpha
           + beta_vix     * VIX_pct_change
           + beta_dxy     * DXY_pct_change
           + beta_credit  * HYG_TLT_ratio_change  (credit risk appetite)
           + beta_yc      * yield_curve_spread_change  (recession/growth signal)

Stage 3: Group stocks by VIX beta quintiles, then compute cross-sectional
         relative strength z-scores within each group.

Academic basis:
  - VIX is the market's "fear gauge" — stocks with high negative VIX beta
    (growth/tech) crash when VIX spikes and rally when it drops.
  - DXY captures USD liquidity cycles — a stronger USD hurts multinational
    earners and EM-exposed stocks.
  - HYG-TLT ratio captures credit risk appetite — widening credit spreads
    hit high-beta names first and hardest.
  - Yield curve slope (10Y-2Y) drives sector rotation — steeping favors
    cyclicals/financials, flattening favors defensives/utilities.
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

    # Filter out macro symbols (all 6: VIX, DXY, HYG, TLT, TNX, IRX)
    macro_syms = {"^VIX", "DX-Y.NYB", "HYG", "TLT", "^TNX", "^IRX"}
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

    # Filter out macro symbols (all 6: VIX, DXY, HYG, TLT, TNX, IRX)
    macro_syms = {"^VIX", "DX-Y.NYB", "HYG", "TLT", "^TNX", "^IRX"}
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


# ── Stage 2: Multi-Factor Macro Betas (v2 — 4-factor) ─────────────────────


def compute_macro_betas(
    stock_returns: pd.DataFrame,
    vix_returns: pd.Series,
    dxy_returns: pd.Series,
    credit_change: pd.Series | None = None,
    yc_change: pd.Series | None = None,
) -> dict:
    """
    Compute multi-factor macro betas for each stock via OLS regression.

    Base model (always): stock_return = alpha + beta_vix * vix_chg + beta_dxy * dxy_chg
    Enhanced model:       + beta_credit * credit_chg + beta_yc * yield_curve_chg

    The new factors are optional — if data is unavailable, the regression
    falls back gracefully to the 2-factor model. This means the screener
    works even when yfinance fails to deliver HYG/TLT/TNX/IRX data.

    Returns {symbol: {"beta_vix": ..., "beta_dxy": ..., "beta_credit": Optional, "beta_yc": Optional, "r_squared": ...}}
    """
    # Align all series on common dates (start with required ones)
    common_dates = stock_returns.index.intersection(vix_returns.index).intersection(dxy_returns.index)

    has_credit = credit_change is not None and len(credit_change) > 0
    has_yc = yc_change is not None and len(yc_change) > 0

    if has_credit:
        common_dates = common_dates.intersection(credit_change.index)
    if has_yc:
        common_dates = common_dates.intersection(yc_change.index)

    stock_returns = stock_returns.loc[common_dates]
    vix_vals = vix_returns.loc[common_dates]
    dxy_vals = dxy_returns.loc[common_dates]

    if len(common_dates) < 30:
        return {}

    # Build design matrix columns
    cols = [np.ones(len(common_dates)), vix_vals.values, dxy_vals.values]
    factor_names = ["alpha", "beta_vix", "beta_dxy"]

    if has_credit:
        cols.append(credit_change.loc[common_dates].values)
        factor_names.append("beta_credit")
    if has_yc:
        cols.append(yc_change.loc[common_dates].values)
        factor_names.append("beta_yc")

    X = np.column_stack(cols)

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

            # R-squared
            y_mean = y_valid.mean()
            ss_total = ((y_valid - y_mean) ** 2).sum()
            ss_residual = (residuals ** 2).sum() if len(residuals) > 0 else 0
            r2 = 1 - ss_residual / ss_total if ss_total > 0 else 0

            result = {
                "beta_vix": round(float(beta[1]), 4),
                "beta_dxy": round(float(beta[2]), 4),
                "r_squared": round(float(r2), 4),
            }
            # Optional factors: include if available
            idx = 3
            if has_credit:
                result["beta_credit"] = round(float(beta[idx]), 4)
                idx += 1
            if has_yc:
                result["beta_yc"] = round(float(beta[idx]), 4)
                idx += 1

            results[sym] = result
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

        # Build stock entries — include optional betas if available
        stock_entries = []
        for sym, _ in batch:
            entry = {
                "symbol": sym,
                "beta_vix": betas[sym]["beta_vix"],
                "beta_dxy": betas[sym]["beta_dxy"],
                "r_squared": betas[sym]["r_squared"],
            }
            if "beta_credit" in betas[sym]:
                entry["beta_credit"] = betas[sym]["beta_credit"]
            if "beta_yc" in betas[sym]:
                entry["beta_yc"] = betas[sym]["beta_yc"]
            stock_entries.append(entry)

        avg_beta_credit = np.mean([
            b[1]["beta_credit"] for b in batch if "beta_credit" in b[1]
        ]) if any("beta_credit" in b[1] for b in batch) else None
        avg_beta_yc = np.mean([
            b[1]["beta_yc"] for b in batch if "beta_yc" in b[1]
        ]) if any("beta_yc" in b[1] for b in batch) else None

        groups[i] = {
            "label": label,
            "n_stocks": len(batch),
            "avg_beta_vix": round(float(avg_beta_vix), 3),
            "avg_beta_dxy": round(float(avg_beta_dxy), 3),
            "stocks": stock_entries,
        }
        if avg_beta_credit is not None:
            groups[i]["avg_beta_credit"] = round(float(avg_beta_credit), 3)
        if avg_beta_yc is not None:
            groups[i]["avg_beta_yc"] = round(float(avg_beta_yc), 3)

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

    # Extract all macro series from MultiIndex panel
    vix_col = None; dxy_col = None
    hyg_col = None; tlt_col = None
    tnx_col = None; irx_col = None

    if isinstance(prices.columns, pd.MultiIndex):
        level_names = prices.columns.names
        price_axis = 0 if level_names[0] in ("Price", "price") else 1
        try:
            macro_close = prices.xs("Close", axis=1, level=price_axis)
            vix_col = macro_close["^VIX"].dropna() if "^VIX" in macro_close.columns else None
            dxy_col = macro_close["DX-Y.NYB"].dropna() if "DX-Y.NYB" in macro_close.columns else None
            hyg_col = macro_close["HYG"].dropna() if "HYG" in macro_close.columns else None
            tlt_col = macro_close["TLT"].dropna() if "TLT" in macro_close.columns else None
            tnx_col = macro_close["^TNX"].dropna() if "^TNX" in macro_close.columns else None
            irx_col = macro_close["^IRX"].dropna() if "^IRX" in macro_close.columns else None
        except KeyError:
            pass

    if vix_col is None or dxy_col is None or len(vix_col) < 30:
        print(f"  WARNING: Missing required macro data (VIX={vix_col is not None}, DXY={dxy_col is not None})")
        return {
            "liquidity_passed": len(passed),
            "total_in_universe": len(symbols),
            "groups": {},
        }

    vix_returns = vix_col.pct_change().dropna()  # VIX % changes (more meaningful beta magnitudes)
    dxy_returns = dxy_col.pct_change().dropna()  # DXY is a price index, % returns are fine

    # Build optional factor series: HYG/TLT credit ratio change
    credit_change = None
    if hyg_col is not None and tlt_col is not None and len(hyg_col) >= 30:
        ratio = hyg_col / tlt_col
        credit_change = ratio.pct_change().dropna()
        print(f"  Macro: HYG/TLT credit ratio available ({len(credit_change)} obs)")

    # Build optional factor series: yield curve spread change (10Y - 3M)
    yc_change = None
    if tnx_col is not None and irx_col is not None and len(tnx_col) >= 30:
        spread = tnx_col - irx_col
        yc_change = spread.diff().dropna()  # absolute change, not pct (yields are already %)
        print(f"  Macro: yield curve spread available ({len(yc_change)} obs)")

    # Stage 2: Multi-factor macro betas
    betas = compute_macro_betas(
        stock_returns[passed], vix_returns, dxy_returns,
        credit_change=credit_change, yc_change=yc_change,
    )
    n_factors = 2 + (1 if credit_change is not None else 0) + (1 if yc_change is not None else 0)
    print(f"  Stage 2 ({n_factors}-factor betas): {len(betas)} symbols")

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
