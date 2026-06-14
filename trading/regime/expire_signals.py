#!/usr/bin/env python3
"""
Auto-expire signals older than 5 trading days that are still pending.

Runs as part of the daily cron (after portfolio sync).
Marks signals as 'expired' so the dashboard only shows actionable items.
"""

import subprocess
from datetime import datetime, timezone, timedelta

import requests

PROJECT_REF = "nwatzlrmoefluymhqgwi"
REST_URL = f"https://{PROJECT_REF}.supabase.co/rest/v1"
EXPIRE_DAYS = 5  # trading days before a pending signal expires


def _get_keychain(service: str = "ats-supabase", account: str = "service_role") -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", service, "-a", account],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _headers():
    key = _get_keychain()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def expire_old_signals() -> int:
    """
    Find all pending signals older than EXPIRE_DAYS and mark them expired.
    Returns number of signals expired.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=EXPIRE_DAYS)).isoformat()

    # Find pending signals older than cutoff
    url = f"{REST_URL}/signals"
    params = {
        "status": "eq.pending",
        "created_at": f"lt.{cutoff}",
        "select": "id",
        "limit": "500",
    }
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    if not resp.ok:
        print(f"  Failed to fetch old signals: {resp.status_code} {resp.text[:200]}")
        return 0

    old = resp.json()
    if not old:
        print("  No pending signals to expire.")
        return 0

    ids = [row["id"] for row in old]
    now = datetime.now(timezone.utc).isoformat()

    # PATCH each id to expired (Supabase REST PATCH requires single-row or bulk by id)
    count = 0
    for sid in ids:
        update = {"status": "expired", "closed_at": now, "close_reason": "expired"}
        patch_resp = requests.patch(
            f"{REST_URL}/signals?id=eq.{sid}",
            headers={**_headers(), "Prefer": "return=representation"},
            json=update,
            timeout=10,
        )
        if patch_resp.ok:
            count += 1

    print(f"  Expired {count}/{len(ids)} signals (>{EXPIRE_DAYS} days old)")
    return count


if __name__ == "__main__":
    print("=" * 40)
    print("  Signal Expiry — auto-expire >5 days")
    print("=" * 40)
    expire_old_signals()
    print("Done.")