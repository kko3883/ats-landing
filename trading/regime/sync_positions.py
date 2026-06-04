"""
Sync Longbridge positions to Supabase portfolio table.
Minimal insert — ticker + quantity only.
"""
import json
import subprocess
from datetime import datetime, timezone

import requests

PROJECT_REF = "nwatzlrmoefluymhqgwi"
REST_URL = f"https://{PROJECT_REF}.supabase.co/rest/v1"


def _get_keychain(service="ats-supabase", account="service_role") -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", service, "-a", account],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def sync():
    print("Fetching positions from Longbridge...")
    result = subprocess.run(
        ["longbridge", "positions", "--format", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"Longbridge error: {result.stderr}")
        return 0

    positions = json.loads(result.stdout)
    if not positions:
        print("No positions found.")
        return 0

    headers = {
        "apikey": _get_keychain(),
        "Authorization": f"Bearer {_get_keychain()}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    rows = []
    for p in positions:
        sym = p.get("symbol", "")
        rows.append({
            "ticker": sym,
            "bucket": "existing",
            "position_qty": int(p.get("quantity", 0)),
            "market_value": float(p.get("market_value", 0)),
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        })

    # Delete old, insert fresh
    try:
        requests.delete(f"{REST_URL}/portfolio", headers=headers, params={"id": "gte.0"}, timeout=10)
    except Exception:
        pass

    resp = requests.post(f"{REST_URL}/portfolio", headers=headers, json=rows, timeout=15)
    if resp.ok:
        count = len(rows)
        print(f"Synced {count} positions to Supabase")
        for r in rows:
            print(f"  {r['ticker']:>10s}  x {r['position_qty']:>5d}  value ${r['market_value']:<10.2f}")
        return count
    else:
        print(f"Supabase error: {resp.status_code} {resp.text[:300]}")
        return 0


if __name__ == "__main__":
    sync()
