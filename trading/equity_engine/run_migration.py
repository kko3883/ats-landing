#!/usr/bin/env python3
"""Migration runner — prints SQL + instructions. One manual step in Supabase dashboard."""
import sys
from pathlib import Path
SQL_FILE = Path(__file__).resolve().parent.parent.parent / "supabase" / "migrations" / "20260622000000_create_equity_engine_state.sql"
PROJECT_REF = "nwatzlrmoefluymhqgwi"

sql = SQL_FILE.read_text()
print(f"""
  ┌─────────────────────────────────────────────────────────────┐
  │  ONE-TIME MIGRATION (30 seconds)                            │
  └─────────────────────────────────────────────────────────────┘

  1. Open:  https://supabase.com/dashboard/project/{PROJECT_REF}/sql/new

  2. Paste and Run:
{sql}

  3. After: https://ats.coolpaperplane.win/equity
""")