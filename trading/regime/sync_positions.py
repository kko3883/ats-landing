#!/usr/bin/env python3
"""
Sync Longbridge positions to Supabase portfolio table.

v2: Adds avg_cost, unrealized P&L, allocation %, and VIX beta zone from
    the latest screener output. Replaces v1's minimal ticker+quantity sync.
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_REF = "nwatzlrmoefluymhqgwi"
REST_URL = f"https://{PROJECT_REF}.supabase.co/rest/v1"
TRADING_DIR = Path.home() / ".hermes" / "trading"
WATCHLIST_FILE = TRADING_DIR / "watchlist.json"


def _get_keychain(service: str = "ats-supabase", account: str = "service_role") -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", service, "-a", account],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _load_vix_zones() -> dict[str, str]:
    """Load ticker→vix_zone mapping from the latest screener watchlist output."""
    if not WATCHLIST_FILE.exists():
        print(f"  No watchlist.json at {WATCHLIST_FILE} — VIX zones unavailable")
        return {}

    try:
        wl = json.loads(WATCHLIST_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    zones = {}
    for market_key in ("us", "hk"):
        market_data = wl.get(market_key, {})
        for group_data in market_data.get("groups", {}).values():
            zone_label = group_data.get("label", "unknown")
            for stock in group_data.get("stocks", []):
                sym = stock.get("symbol", "")
                if sym:
                    # Normalize: screener uses bare symbols (AAPL), Longbridge
                    # uses .US/.HK suffix. Store both forms.
                    zones[sym] = zone_label
                    zones[f"{sym}.US"] = zone_label
                    zones[f"{sym}.HK"] = zone_label
                    # Also handle screener's own suffix format
                    sym_upper = sym.upper()
                    zones[sym_upper] = zone_label
                    zones[f"{sym_upper}.US"] = zone_label
                    zones[f"{sym_upper}.HK"] = zone_label
    return zones


def _headers():
    key = _get_keychain()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def sync() -> int:
    print("Fetching positions from Longbridge...")
    result = subprocess.run(
        ["longbridge", "positions", "--format", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"  Longbridge error: {result.stderr}")
        return 0

    positions = json.loads(result.stdout)
    if not positions:
        print("  No positions found.")
        # Still clear the portfolio table (positions were closed)
        try:
            requests.delete(
                f"{REST_URL}/portfolio",
                headers=_headers(),
                params={"id": "gte.0"},
                timeout=10,
            )
        except Exception:
            pass
        print("  Portfolio table cleared.")
        return 0

    # Load VIX zone map from latest screener
    vix_zones = _load_vix_zones()
    if vix_zones:
        print(f"  Loaded {len(vix_zones)} ticker→vix_zone mappings from screener")

    now = datetime.now(timezone.utc).isoformat()

    rows = []
    total_value = 0.0

    # First pass: collect data and compute total portfolio value
    raw_rows = []
    for p in positions:
        sym = p.get("symbol", "")
        qty = int(p.get("quantity", 0))
        mkt_val = float(p.get("market_value", 0))
        last_price = float(p.get("last_price", 0) or p.get("last", 0) or 0)
        avg_cost = float(p.get("avg_cost", 0) or p.get("cost_price", 0) or 0)

        # If last_price not provided but mkt_val and qty are, derive it
        if not last_price and mkt_val and qty > 0:
            last_price = mkt_val / qty

        total_value += mkt_val

        # Look up VIX zone
        vix_zone = vix_zones.get(sym, vix_zones.get(sym.upper(), ""))

        raw_rows.append({
            "sym": sym,
            "qty": qty,
            "mkt_val": mkt_val,
            "last_price": last_price,
            "avg_cost": avg_cost,
            "vix_zone": vix_zone,
        })

    # Second pass: compute allocation % and unrealized P&L
    for r in raw_rows:
        alloc_pct = round((r["mkt_val"] / total_value * 100), 2) if total_value > 0 else 0.0

        unrealized = None
        if r["avg_cost"] > 0 and r["last_price"] > 0 and r["qty"] > 0:
            unrealized = round((r["last_price"] - r["avg_cost"]) * r["qty"], 2)

        rows.append({
            "ticker": r["sym"],
            "quantity": r["qty"],
            "avg_cost": round(r["avg_cost"], 2) if r["avg_cost"] > 0 else None,
            "market_value": round(r["mkt_val"], 2),
            "last_price": round(r["last_price"], 2) if r["last_price"] > 0 else None,
            "unrealized_pnl": unrealized,
            "allocation_pct": alloc_pct,
            "vix_zone": r["vix_zone"] or None,
            "bucket": "existing",
            "snapshot_at": now,
        })

    # Delete old rows, insert fresh
    try:
        requests.delete(
            f"{REST_URL}/portfolio",
            headers=_headers(),
            params={"id": "gte.0"},
            timeout=10,
        )
    except Exception as e:
        print(f"  Delete old rows failed (may be empty table): {e}")

    if not rows:
        print("  No valid rows to sync.")
        return 0

    resp = requests.post(f"{REST_URL}/portfolio", headers=_headers(), json=rows, timeout=15)
    if resp.ok:
        count = len(rows)
        print(f"\n  Synced {count} positions to Supabase")
        print(f"  {'Ticker':>10s}  {'Qty':>6s}  {'Value':>12s}  {'Alloc':>7s}  {'PnL':>10s}  {'VIX Zone':>14s}")
        print(f"  {'─'*10}  {'─'*6}  {'─'*12}  {'─'*7}  {'─'*10}  {'─'*14}")
        for r in rows:
            pnl_str = f"${r['unrealized_pnl']:+,.0f}" if r["unrealized_pnl"] is not None else "—"
            zone_str = r["vix_zone"] or "—"
            print(f"  {r['ticker']:>10s}  {r['quantity']:>6d}  ${r['market_value']:>11,.2f}  {r['allocation_pct']:>5.1f}%  {pnl_str:>10s}  {zone_str:>14s}")

        # Print concentration warning if any group > 40%
        _check_concentration(rows)
        return count
    else:
        print(f"  Supabase error: {resp.status_code} {resp.text[:300]}")
        return 0


def _check_concentration(rows: list[dict]) -> None:
    """Warn if portfolio is over-concentrated in one VIX beta group."""
    group_value = {}
    total = 0.0
    for r in rows:
        zone = r.get("vix_zone") or "unknown"
        group_value[zone] = group_value.get(zone, 0.0) + r["market_value"]
        total += r["market_value"]

    if total == 0:
        return

    for zone, val in sorted(group_value.items(), key=lambda x: x[1], reverse=True):
        pct = val / total * 100
        flag = " ⚠ OVER-CONCENTRATED" if pct > 40 else ""
        print(f"  VIX zone '{zone}': {pct:5.1f}% of portfolio{flag}")


if __name__ == "__main__":
    sync()