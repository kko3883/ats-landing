#!/bin/bash
# Migration helper — prints the SQL you paste into Supabase once.
# Usage: sh equity_engine/run_migration.sh

PROJECT_REF="nwatzlrmoefluymhqgwi"
SQL_FILE="$(dirname "$0")/../../supabase/migrations/20260622000000_create_equity_engine_state.sql"

echo ""
echo "  ┌─────────────────────────────────────────────────────────────┐"
echo "  │  ONE-TIME MIGRATION (30 seconds)                            │"
echo "  └─────────────────────────────────────────────────────────────┘"
echo ""
echo "  1. Open:  https://supabase.com/dashboard/project/${PROJECT_REF}/sql/new"
echo ""
echo "  2. Paste and run the SQL below:"
echo ""
echo "  ═══════════════════════════════════════════════════════════════"
cat "$SQL_FILE"
echo "  ═══════════════════════════════════════════════════════════════"
echo ""
echo "  3. After migration, the dashboard is live at:"
echo "     https://ats.coolpaperplane.win/equity"
echo ""