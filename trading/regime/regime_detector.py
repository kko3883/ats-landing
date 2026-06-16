#!/usr/bin/env python3
"""
Regime detector — multi-factor market regime classification.

Five factors → one regime label:
  1. VIX level        — <15 trending, 15-25 choppy, >25 risk-off, >35 crisis
  2. HYG-TLT spread   — widening = credit stress → pushes toward choppy/crisis
  3. US10Y-US2Y spread — yield curve. Inversion (<0) = recession signal → risk_off.
     Steepening (>1.0) = recovery → supports risk_on.
  4. SPY-QQQ corr     — >0.7 = single-threaded market → choppy (no alpha from selection)
  5. VIX momentum     — 5-day % change > +30% = crisis signal
     (VIX backwardation is the gold standard but yfinance free tier doesn't
      reliably provide VIX futures. 5-day momentum is the best free proxy.)

Regimes:
  crisis    —  VIX > 35 OR (VIX momentum panic AND HYG-TLT spread wide)
  risk_off  —  VIX > 25 OR HYG-TLT spread > 2σ from mean OR yield curve inverted
  choppy    —  VIX 15-25 OR SPY-QQQ corr > 0.7
  risk_on   —  VIX < 15 AND spread normal AND corr < 0.7 AND curve not inverted

Publishes to the `regime` table in Supabase.
Dashboard reads the latest row to display the regime banner + activated groups.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

TRADING_DIR = Path.home() / ".hermes" / "trading"
PROJECT_REF = "nwatzlrmoefluymhqgwi"
REST_URL = f"https://{PROJECT_REF}.supabase.co/rest/v1"

# Regime definitions (activated VIX-beta groups per regime)
REGIMES = {
    "crisis": {
        "description": "Extreme volatility / credit stress — capital preservation mode",
        "activated_groups": ["defensive"],
        "deactivated_groups": ["high_beta_growth", "moderate_growth", "neutral", "moderate_defensive"],
        "long_bias": "defensive",
        "short_bias": "high_beta_growth, moderate_growth",
    },
    "risk_off": {
        "description": "High volatility, flight to safety",
        "activated_groups": ["defensive", "moderate_defensive"],
        "deactivated_groups": ["high_beta_growth", "moderate_growth"],
        "long_bias": "defensive, moderate_defensive",
        "short_bias": "high_beta_growth, moderate_growth",
    },
    "choppy": {
        "description": "Moderate volatility, range-bound / single-threaded market",
        "activated_groups": ["neutral", "moderate_defensive"],
        "deactivated_groups": ["high_beta_growth"],
        "long_bias": "neutral, moderate_defensive",
        "short_bias": "none",
    },
    "risk_on": {
        "description": "Low volatility, broad risk appetite",
        "activated_groups": ["high_beta_growth", "moderate_growth"],
        "deactivated_groups": ["moderate_defensive", "defensive"],
        "long_bias": "high_beta_growth, moderate_growth",
        "short_bias": "defensive",
    },
}


def _get_keychain(service: str = "ats-supabase", account: str = "service_role") -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", service, "-a", account],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# ═══════════════════════════════════════════════════════════════════════════
# Factor 1: VIX level (unchanged from v1 — still the primary signal)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_vix() -> float | None:
    """Fetch latest VIX close. yfinance first, Longbridge fallback."""
    try:
        vix = yf.download("^VIX", period="5d", interval="1d", progress=False)
        if not vix.empty:
            close_col = vix['Close']
            val = close_col.iloc[:, 0] if isinstance(close_col, pd.DataFrame) else close_col
            return float(val.iloc[-1])
    except Exception as e:
        print(f"  yfinance VIX failed: {e}")

    try:
        result = subprocess.run(
            ["longbridge", "quote", "VIX.US", "--format", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data:
                return float(data[0].get("last", 0))
    except Exception:
        pass
    return None


def fetch_vix_momentum() -> float | None:
    """VIX 5-day % change. >30% = panic / crisis signal. Returns None on failure."""
    try:
        vix = yf.download("^VIX", period="10d", interval="1d", progress=False)
        if vix.empty or len(vix) < 6:
            return None
        close_col = vix['Close']
        closes = close_col.iloc[:, 0] if isinstance(close_col, pd.DataFrame) else close_col
        vix_now = float(closes.iloc[-1])
        vix_5d_ago = float(closes.iloc[-6])
        if vix_5d_ago <= 0:
            return None
        return (vix_now - vix_5d_ago) / vix_5d_ago * 100
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Factor 2: HYG-TLT credit spread
# ═══════════════════════════════════════════════════════════════════════════

def fetch_hyg_tlt_spread() -> dict | None:
    """
    HYG-TLT ratio = HYG_price / TLT_price.
    Falling ratio = credit stress (HYG drops, TLT rises as flight-to-quality).
    Returns {ratio, zscore, wide} or None.
    """
    try:
        data = yf.download(["HYG", "TLT"], period="3mo", interval="1d", progress=False)
        if data.empty:
            return None
        close = data['Close']
        if isinstance(close, pd.DataFrame) and close.shape[1] >= 2:
            hyg = close.iloc[:, 0]
            tlt = close.iloc[:, 1]
        else:
            return None

        ratio = hyg / tlt
        current = float(ratio.iloc[-1])
        mean = float(ratio.rolling(20).mean().iloc[-1])
        std = float(ratio.rolling(20).std().iloc[-1])

        if std == 0:
            return {"ratio": round(current, 4), "zscore": 0, "wide": False}

        zscore = (current - mean) / std
        # Negative zscore = ratio falling = HYG underperforming TLT = credit stress
        wide = zscore < -1.5  # >1.5σ below 20-day mean = stress signal
        return {"ratio": round(current, 4), "zscore": round(zscore, 2), "wide": wide}
    except Exception as e:
        print(f"  HYG-TLT failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Factor 3: SPY-QQQ 20-day rolling correlation
# ═══════════════════════════════════════════════════════════════════════════

def fetch_spy_qqq_correlation() -> float | None:
    """
    SPY-QQQ 20-day Pearson r on daily returns.
    >0.7 = single-threaded market (everything moves together → no alpha from selection).
    Returns correlation float or None.
    """
    try:
        data = yf.download(["SPY", "QQQ"], period="2mo", interval="1d", progress=False)
        if data.empty:
            return None
        close = data['Close']
        if isinstance(close, pd.DataFrame) and close.shape[1] >= 2:
            spy = close.iloc[:, 0].pct_change().dropna()
            qqq = close.iloc[:, 1].pct_change().dropna()
        else:
            return None

        common = spy.index.intersection(qqq.index)
        if len(common) < 20:
            return None

        corr = spy.loc[common[-20:]].corr(qqq.loc[common[-20:]])
        return round(float(corr), 3)
    except Exception as e:
        print(f"  SPY-QQQ correlation failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Factor 4: US10Y-US2Y yield curve spread
# ═══════════════════════════════════════════════════════════════════════════

def fetch_yield_curve() -> dict | None:
    """
    US10Y-US2Y yield curve spread.
    Inversion (spread < 0) = recession signal, the single best macro warning.
    Steepening (spread > 1.0) = recovery, supports risk-on positioning.

    Uses ^TNX (10Y CBOE index) and ^IRX (13-week T-bill, proxy for 2Y short rate
    since yfinance doesn't have a clean ^FVX or ^UST2Y). The absolute level will
    differ from the true 10Y-2Y, but the *direction and sign changes* are highly
    correlated — and that's what matters for regime classification.

    Returns {spread, inverted, steep} or None.
    """
    try:
        data = yf.download(["^TNX", "^IRX"], period="3mo", interval="1d", progress=False)
        if data.empty:
            return None
        close = data['Close']
        if isinstance(close, pd.DataFrame) and close.shape[1] >= 2:
            tnx = close.iloc[:, 0]  # 10Y yield (in %)
            irx = close.iloc[:, 1]  # 13-week T-bill yield (in %)
        else:
            return None

        spread = float(tnx.iloc[-1]) - float(irx.iloc[-1])
        inverted = spread < 0
        steep = spread > 1.0
        return {
            "spread": round(spread, 2),
            "tnx_10y": round(float(tnx.iloc[-1]), 2),
            "irx_3m": round(float(irx.iloc[-1]), 2),
            "inverted": inverted,
            "steep": steep,
        }
    except Exception as e:
        print(f"  Yield curve failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Classification (multi-factor)
# ═══════════════════════════════════════════════════════════════════════════

def classify_regime(
    vix: float,
    vix_momentum: float | None = None,
    hyg_tlt: dict | None = None,
    spy_qqq_corr: float | None = None,
    yield_curve: dict | None = None,
) -> tuple[str, dict]:
    """
    Multi-factor regime classification.

    Priority order (first match wins):
      crisis   — VIX > 35, OR VIX momentum panic AND credit spread wide
      risk_off — VIX > 25, OR credit spread wide, OR yield curve inverted
      choppy   — VIX 15-25, OR SPY-QQQ correlation > 0.7
      risk_on  — everything else (VIX < 15 is the gate)
    """
    # Crisis checks
    if vix > 35:
        return "crisis", REGIMES["crisis"]
    if vix_momentum is not None and vix_momentum > 30 and hyg_tlt and hyg_tlt.get("wide"):
        return "crisis", REGIMES["crisis"]

    # Risk-off checks
    if vix > 25:
        return "risk_off", REGIMES["risk_off"]
    if hyg_tlt and hyg_tlt.get("wide"):
        return "risk_off", REGIMES["risk_off"]
    if yield_curve and yield_curve.get("inverted"):
        return "risk_off", REGIMES["risk_off"]

    # Choppy checks
    if vix >= 15:
        return "choppy", REGIMES["choppy"]
    if spy_qqq_corr is not None and spy_qqq_corr > 0.7:
        return "choppy", REGIMES["choppy"]

    # Default: risk-on (but steep yield curve gives extra confidence)
    return "risk_on", REGIMES["risk_on"]


# ═══════════════════════════════════════════════════════════════════════════
# Supabase publisher
# ═══════════════════════════════════════════════════════════════════════════

def publish(
    regime_name: str,
    vix_level: float,
    activated: list[str],
    factors: dict | None = None,
):
    """Write regime state to Supabase regime table."""
    headers = {
        "apikey": _get_keychain(),
        "Authorization": f"Bearer {_get_keychain()}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    row = {
        "regime_name": regime_name,
        "vix_level": round(vix_level, 2),
        "description": REGIMES[regime_name]["description"],
        "activated_groups": activated,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    resp = requests.post(
        f"{REST_URL}/regime",
        headers=headers,
        json=[row],
        timeout=15,
    )
    if resp.ok:
        print(f"  Supabase: regime '{regime_name}' written (VIX={vix_level:.1f})")
    else:
        print(f"  Supabase write failed: {resp.status_code} {resp.text[:200]}")


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 50)
    print("  ATS Regime Detector v3 — 5-factor")
    print("=" * 50)

    # Factor 1: VIX
    print("\n[1/5] Fetching VIX...")
    vix = fetch_vix()
    if vix is None:
        print("  Could not fetch VIX. Defaulting to 20.0 (choppy).")
        vix = 20.0
    print(f"  VIX = {vix:.2f}")

    # Factor 2: VIX momentum (proxy for backwardation)
    print("\n[2/5] Fetching VIX 5-day momentum...")
    vix_mom = fetch_vix_momentum()
    if vix_mom is not None:
        print(f"  VIX 5d change = {vix_mom:+.1f}%")
    else:
        print("  VIX momentum unavailable — skipping")

    # Factor 3: HYG-TLT credit spread
    print("\n[3/5] Fetching HYG-TLT credit spread...")
    hyg_tlt = fetch_hyg_tlt_spread()
    if hyg_tlt is not None:
        status = "⚠ WIDE (credit stress)" if hyg_tlt["wide"] else "✓ normal"
        print(f"  HYG/TLT = {hyg_tlt['ratio']} (z={hyg_tlt['zscore']}) — {status}")
    else:
        print("  HYG-TLT unavailable — skipping")

    # Factor 4: US10Y-US2Y yield curve
    print("\n[4/5] Fetching US10Y-US2Y yield curve...")
    yc = fetch_yield_curve()
    if yc is not None:
        status = "⚠ INVERTED" if yc["inverted"] else ("▲ STEEP" if yc["steep"] else "✓ normal")
        print(f"  10Y={yc['tnx_10y']}%  3M={yc['irx_3m']}%  spread={yc['spread']:+.2f}% — {status}")
    else:
        print("  Yield curve unavailable — skipping")

    # Factor 5: SPY-QQQ correlation
    print("\n[5/5] Fetching SPY-QQQ 20d correlation...")
    corr = fetch_spy_qqq_correlation()
    if corr is not None:
        status = "⚠ SINGLE-THREADED" if corr > 0.7 else "✓ normal"
        print(f"  SPY-QQQ corr = {corr:.3f} — {status}")
    else:
        print("  SPY-QQQ correlation unavailable — skipping")

    # Classify
    name, cfg = classify_regime(vix, vix_mom, hyg_tlt, corr, yc)
    print(f"\n  → Regime: {name.upper()} — {cfg['description']}")
    print(f"  Activated: {', '.join(cfg['activated_groups'])}")
    print(f"  Long bias: {cfg['long_bias']}")
    print(f"  Short bias: {cfg['short_bias']}")

    # Build factors dict for Supabase (debuggable)
    factors = {"vix": round(vix, 2)}
    if vix_mom is not None:
        factors["vix_5d_momentum_pct"] = round(vix_mom, 1)
    if hyg_tlt is not None:
        factors["hyg_tlt_ratio"] = hyg_tlt["ratio"]
        factors["hyg_tlt_zscore"] = hyg_tlt["zscore"]
    if yc is not None:
        factors["yc_spread"] = yc["spread"]
        factors["yc_inverted"] = yc["inverted"]
    if corr is not None:
        factors["spy_qqq_corr_20d"] = corr

    publish(name, vix, cfg["activated_groups"], factors)
    print("\nDone.")


if __name__ == "__main__":
    main()