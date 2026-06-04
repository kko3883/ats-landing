"""
Regime detector — determines current market regime from VIX level.

Three regimes:
  - Risk-On:    VIX < 15  → Favor high_beta/moderate growth stocks (long)
  - Choppy:     VIX 15-25 → Favor neutral/defensive stocks
  - Risk-Off:   VIX > 25  → Defensive only, short high-beta

Publishes to the `regime` table in Supabase.
Dashboard uses this to highlight which VIX beta groups are active.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

TRADING_DIR = Path.home() / ".hermes" / "trading"
PROJECT_REF = "nwatzlrmoefluymhqgwi"
REST_URL = f"https://{PROJECT_REF}.supabase.co/rest/v1"

# Regime definitions
REGIMES = {
    "risk_on": {
        "description": "Low volatility, risk appetite high",
        "condition": lambda vix: vix < 15,
        "activated_groups": ["high_beta_growth", "moderate_growth"],
        "deactivated_groups": ["moderate_defensive", "defensive"],
        "long_bias": "high_beta, moderate_growth",
        "short_bias": "defensive",
    },
    "choppy": {
        "description": "Moderate volatility, range-bound market",
        "condition": lambda vix: 15 <= vix <= 25,
        "activated_groups": ["neutral", "moderate_defensive"],
        "deactivated_groups": ["high_beta_growth"],
        "long_bias": "neutral, moderate_defensive",
        "short_bias": "none",
    },
    "risk_off": {
        "description": "High volatility, flight to safety",
        "condition": lambda vix: vix > 25,
        "activated_groups": ["defensive", "moderate_defensive"],
        "deactivated_groups": ["high_beta_growth", "moderate_growth"],
        "long_bias": "defensive",
        "short_bias": "high_beta_growth, moderate_growth",
    },
}


def _get_keychain(service="ats-supabase", account="service_role") -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", service, "-a", account],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def fetch_vix() -> float | None:
    """Fetch VIX level from yfinance."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", """
import yfinance as yf
vix = yf.download("^VIX", period="2d", interval="1d")
if not vix.empty:
    print(f"CLOSE:{vix['Close'].values[-1].item()}")
else:
    print("null")
"""],
            capture_output=True, text=True, timeout=30,
        )
        for line in result.stdout.strip().splitlines():
            if line.startswith("CLOSE:"):
                return float(line.split(":")[1])
    except Exception as e:
        print(f"  yfinance VIX failed: {e}")

    # Fallback: try Longbridge
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


def classify_regime(vix: float) -> tuple[str, dict]:
    """Classify VIX level into a regime."""
    for name, cfg in REGIMES.items():
        if cfg["condition"](vix):
            return name, cfg
    return "choppy", REGIMES["choppy"]


def publish(regime_name: str, vix_level: float, activated: list[str]):
    """Write regime state to Supabase."""
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
        print(f"  Supabase write failed: {resp.status_code}")


def main():
    print("Fetching VIX...")
    vix = fetch_vix()
    if vix is None:
        print("  Could not fetch VIX. Defaulting to 'choppy'.")
        vix = 20.0
    else:
        print(f"  VIX = {vix:.2f}")

    name, cfg = classify_regime(vix)
    print(f"  Regime: {name} — {cfg['description']}")
    print(f"  Activated groups: {', '.join(cfg['activated_groups'])}")
    print(f"  Long bias: {cfg['long_bias']}")
    print(f"  Short bias: {cfg['short_bias']}")

    publish(name, vix, cfg["activated_groups"])
    print("Done.")


if __name__ == "__main__":
    main()
