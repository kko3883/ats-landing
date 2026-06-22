#!/bin/bash
# Migration helper — prints the SQL you paste into Supabase once.
# Usage: sh equity_engine/run_migration.sh

PROJECT_REF="nwatzlrmoefluymhqgwi"

echo ""
echo "  ┌─────────────────────────────────────────────────────────────┐"
echo "  │  STEP 1 — Run this in Supabase SQL Editor                   │"
echo "  └─────────────────────────────────────────────────────────────┘"
echo ""
echo "  Open:  https://supabase.com/dashboard/project/${PROJECT_REF}/sql/new"
echo ""
echo "  Paste and run this single line (table already created, just enable realtime):"
echo ""
echo "  ═══════════════════════════════════════════════════════════════"
echo "  ALTER publication supabase_realtime ADD TABLE equity_state;"
echo "  ═══════════════════════════════════════════════════════════════"
echo ""
echo "  If it says 'already a member' — you're done. That's all."
echo ""
echo "  ┌─────────────────────────────────────────────────────────────┐"
echo "  │  STEP 2 — The dashboard is live at                          │"
echo "  └─────────────────────────────────────────────────────────────┘"
echo ""
echo "  https://ats.coolpaperplane.win/equity"
echo ""
echo "  (It'll show 'Offline — no data' until the engine writes its first snapshot.)"
echo ""