"""
Shared credential loader for all ATS Python scripts.

Replaces the old keychain-only approach with a file-based system.
All scripts in regime/, watchlist/, and trading/ should import from here.

Priority:
  1. .env.supabase file (recommended — gitignored)
  2. macOS Keychain (legacy fallback)
  3. Environment variables (last resort)
"""
import os
import subprocess
from pathlib import Path

# ── Locate the repo root ──────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env.supabase"


# ── Public: get creds ─────────────────────────────────────────────────────

def get_supabase_url() -> str:
    """Supabase project URL."""
    if ENV_FILE.exists():
        url = _read_env("SUPABASE_URL")
        if url:
            return url
    return os.environ.get("SUPABASE_URL", "https://nwatzlrmoefluymhqgwi.supabase.co")


def get_anon_key() -> str:
    """Public anon key — for dashboard reads + public API."""
    if ENV_FILE.exists():
        key = _read_env("SUPABASE_ANON_KEY")
        if key and key not in ("", "your-anon-key-here"):
            return key
    return os.environ.get(
        "SUPABASE_ANON_KEY",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im53YXR6bHJtb2VmbHV5bWhxZ3dpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODAyNjE0MjMsImV4cCI6MjA5NTgzNzQyM30.JLjcTg-vFHwNcC0xjRrnL-77zv-bbFcIOEAL588lYPM",
    )


def get_service_role_key() -> str | None:
    """
    Service role key — for engine writes + SQL operations.
    NEVER commit this. NEVER expose it in client-side code.

    Priority: .env.supabase file → keychain → env var.
    """
    # 1. Try .env.supabase file
    if ENV_FILE.exists():
        key = _read_env("SUPABASE_SERVICE_ROLE_KEY")
        if key and key not in ("", "your-service-role-key-here"):
            return key

    # 2. Try macOS Keychain (legacy)
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", "ats-supabase", "-a", "service_role"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    # 3. Try environment variable
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY")


def get_supabase_headers(auth_level: str = "anon") -> dict[str, str]:
    """HTTP headers for Supabase REST API."""
    if auth_level == "service":
        key = get_service_role_key()
    else:
        key = get_anon_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ── Private ────────────────────────────────────────────────────────────────

def _read_env(key: str) -> str | None:
    """Read a KEY=VALUE line from .env.supabase."""
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return None