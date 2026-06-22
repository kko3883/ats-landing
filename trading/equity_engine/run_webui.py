#!/usr/bin/env python3
"""
Web Dashboard for the Equity Trading Engine.

Serves a real-time HTML dashboard at http://localhost:8080 that shows:
  - Account equity & P&L
  - Active positions (symbol, entry, stop, trail, P&L)
  - Recent trade log
  - Layer 1/2/3 activity counters
  - Regime status

Zero dependencies beyond Python stdlib.  Reads state.json and trades.jsonl.

Usage:
    python equity_engine/run_webui.py
    python equity_engine/run_webui.py --port 8080
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from equity_engine.config import STATE_FILE, TRADES_LOG

# ── HTML template ───────────────────────────────────────────────────────────

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Equity Engine — Dashboard</title>
<style>
  :root {
    --bg: #0d1117;
    --card: #161b22;
    --border: #30363d;
    --text: #c9d1d9;
    --muted: #8b949e;
    --green: #3fb950;
    --red: #f85149;
    --yellow: #d2991d;
    --blue: #58a6ff;
    --purple: #bc8cff;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; padding:20px; }
  .header { display:flex; justify-content:space-between; align-items:center; margin-bottom:24px; }
  .header h1 { font-size:24px; font-weight:700; }
  .status-dot { width:10px; height:10px; border-radius:50%; display:inline-block; margin-right:6px; }
  .status-dot.live { background:var(--green); animation:pulse 2s infinite; }
  .status-dot.offline { background:var(--red); }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; margin-bottom:24px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:18px; }
  .card .label { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px; }
  .card .value { font-size:28px; font-weight:700; }
  .card .sub { font-size:11px; color:var(--muted); margin-top:4px; }
  .green { color:var(--green); }
  .red { color:var(--red); }
  .yellow { color:var(--yellow); }
  .blue { color:var(--blue); }
  .purple { color:var(--purple); }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:0.5px; padding:8px 12px; border-bottom:1px solid var(--border); }
  td { padding:10px 12px; border-bottom:1px solid var(--border); font-family:'SF Mono',Monaco,monospace; font-size:12px; }
  tr:hover { background:rgba(255,255,255,0.02); }
  .section-title { font-size:14px; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:12px; margin-top:28px; }
  .trade-entry { font-size:11px; padding:4px 0; border-bottom:1px solid rgba(48,54,61,0.5); }
  .trade-entry .ts { color:var(--muted); margin-right:8px; }
  .regime-badge { display:inline-block; padding:4px 12px; border-radius:20px; font-size:12px; font-weight:600; }
  .regime-badge.risk_on { background:rgba(63,185,80,0.15); color:var(--green); border:1px solid rgba(63,185,80,0.3); }
  .regime-badge.choppy { background:rgba(210,153,29,0.15); color:var(--yellow); border:1px solid rgba(210,153,29,0.3); }
  .regime-badge.risk_off { background:rgba(248,81,73,0.15); color:var(--red); border:1px solid rgba(248,81,73,0.3); }
  .regime-badge.crisis { background:rgba(248,81,73,0.25); color:var(--red); border:1px solid rgba(248,81,73,0.5); }
  .empty-state { text-align:center; padding:40px; color:var(--muted); }
  .refresh { font-size:11px; color:var(--muted); }
  .pnl-bar { height:4px; border-radius:2px; margin-top:4px; }
  .progress-bar { height:6px; background:var(--border); border-radius:3px; overflow:hidden; margin-top:4px; }
  .progress-bar .fill { height:100%; border-radius:3px; background:var(--green); transition:width 0.5s; }
  .progress-bar .fill.warn { background:var(--yellow); }
  .progress-bar .fill.danger { background:var(--red); }
  .layer-grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-bottom:16px; }
  .layer-card { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:12px; text-align:center; }
  .layer-card .layer-name { font-size:10px; color:var(--muted); text-transform:uppercase; letter-spacing:0.5px; }
  .layer-card .layer-val { font-size:20px; font-weight:700; margin-top:4px; }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>📊 Equity Trading Engine</h1>
    <div style="margin-top:4px;display:flex;align-items:center;">
      <span class="status-dot live" id="dot"></span>
      <span class="refresh" id="status-text">Connected</span>
    </div>
  </div>
  <div style="text-align:right;">
    <div style="font-size:11px;color:var(--muted);">Last update</div>
    <div id="last-update" style="font-size:13px;">--</div>
  </div>
</div>

<!-- KPI Cards -->
<div class="grid" id="kpi-cards"></div>

<!-- Layer Status -->
<div class="layer-grid" id="layer-status"></div>

<!-- Positions Table -->
<div>
  <div class="section-title">📈 Active Positions</div>
  <div id="positions-table"></div>
</div>

<!-- Trade Log -->
<div>
  <div class="section-title">📋 Recent Trade Log</div>
  <div id="trade-log"></div>
</div>

<!-- Progress -->
<div style="margin-top:16px;">
  <div style="font-size:11px;color:var(--muted);margin-bottom:4px;">Risk Budget</div>
  <div class="progress-bar"><div class="fill" id="risk-bar" style="width:0%"></div></div>
</div>

<script>
const API = '/api/state';

function formatMoney(v) {
  if (v == null) return '—';
  return '$' + Number(v).toLocaleString(undefined, {minimumFractionDigits:2,maximumFractionDigits:2});
}
function formatPct(v) {
  if (v == null) return '—';
  const pct = (Number(v)*100).toFixed(2);
  return (Number(v)>=0?'+':'') + pct + '%';
}
function colorPnl(v) {
  if (v == null || v == 0) return '';
  return Number(v) >= 0 ? 'green' : 'red';
}

async function refresh() {
  try {
    const resp = await fetch(API);
    const s = await resp.json();
    document.getElementById('dot').className = 'status-dot live';
    document.getElementById('status-text').textContent = 'Live';
    document.getElementById('last-update').textContent = s.timestamp ? s.timestamp.slice(0,19).replace('T',' ') : '--';

    // KPI cards
    const kpi = document.getElementById('kpi-cards');
    const equity = s.equity || 100000;
    const startEquity = s.starting_equity || 100000;
    const dailyPnl = s.starting_equity ? (equity - startEquity) : 0;
    const dailyPnlPct = s.starting_equity ? (equity/startEquity - 1) : 0;

    kpi.innerHTML = `
      <div class="card">
        <div class="label">Portfolio Equity</div>
        <div class="value">${formatMoney(equity)}</div>
        <div class="sub">Daily P&L: <span class="${colorPnl(dailyPnl)}">${formatMoney(dailyPnl)} (${formatPct(dailyPnlPct)})</span></div>
      </div>
      <div class="card">
        <div class="label">Active Positions</div>
        <div class="value blue">${s.position_count || 0}</div>
        <div class="sub">Max: 5</div>
      </div>
      <div class="card">
        <div class="label">Total Trades</div>
        <div class="value purple">${s.trade_count || 0}</div>
        <div class="sub">All time</div>
      </div>
      <div class="card">
        <div class="label">Engine Status</div>
        <div class="value ${s.paused?'red':'green'}">${s.paused ? '⏸ PAUSED' : '▶ RUNNING'}</div>
        <div class="sub">${s.pause_reason || ''}</div>
      </div>
    `;

    // Layer status
    const layers = document.getElementById('layer-status');
    const l1 = s.layer1 || {};
    const l2 = s.layer2 || {};
    const l3 = s.layer3 || {};
    layers.innerHTML = `
      <div class="layer-card">
        <div class="layer-name">Layer 1 — Macro</div>
        <div class="layer-val">${l1.approved || 0} approved</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px;">SMA(200) filter</div>
      </div>
      <div class="layer-card">
        <div class="layer-name">Layer 2 — Tactical</div>
        <div class="layer-val">${l2.signals_above || 0} signals</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px;">XGBoost prob > 0.65</div>
      </div>
      <div class="layer-card">
        <div class="layer-name">Layer 3 — Micro</div>
        <div class="layer-val">${l3.exits || 0} exits</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px;">Trailing stops managed</div>
      </div>
    `;

    // Positions
    const posDiv = document.getElementById('positions-table');
    const positions = s.positions || {};
    const posKeys = Object.keys(positions);
    if (posKeys.length === 0) {
      posDiv.innerHTML = '<div class="empty-state">No active positions</div>';
    } else {
      let html = '<table><thead><tr><th>Symbol</th><th>Side</th><th>Entry</th><th>Stop</th><th>Trail</th><th>Qty</th><th>Bars</th></tr></thead><tbody>';
      for (const [sym, pos] of Object.entries(positions)) {
        html += `<tr>
          <td style="font-weight:600;">${sym}</td>
          <td class="${pos.side==='LONG'?'green':'red'}">${pos.side||'LONG'}</td>
          <td>${formatMoney(pos.entry_price)}</td>
          <td class="red">${formatMoney(pos.stop_loss)}</td>
          <td class="yellow">${formatMoney(pos.trailing_stop)}</td>
          <td>${pos.quantity||0}</td>
          <td>${pos.bars_held||0}</td>
        </tr>`;
      }
      html += '</tbody></table>';
      posDiv.innerHTML = html;
    }

    // Trade log
    const logDiv = document.getElementById('trade-log');
    const trades = s.recent_trades || [];
    if (trades.length === 0) {
      logDiv.innerHTML = '<div class="empty-state">No trades recorded yet</div>';
    } else {
      let html = '';
      for (const t of trades.slice(-15)) {
        const ts = (t.ts||'').slice(0,19).replace('T',' ');
        const sym = t.symbol || '';
        const event = t.event || '';
        const pnl = t.pnl_dollar != null ? formatMoney(t.pnl_dollar) : '';
        const reason = t.exit_reason || t.reason || '';
        const pnlColor = t.pnl_dollar != null ? colorPnl(t.pnl_dollar) : '';
        html += `<div class="trade-entry">
          <span class="ts">${ts}</span>
          <span style="font-weight:600;">${sym}</span>
          <span style="margin-left:8px;">${event}</span>
          ${pnl ? `<span class="${pnlColor}" style="margin-left:8px;">${pnl}</span>` : ''}
          ${reason ? `<span style="color:var(--muted);margin-left:8px;">[${reason}]</span>` : ''}
        </div>`;
      }
      logDiv.innerHTML = html;
    }

    // Risk bar
    const bar = document.getElementById('risk-bar');
    const posCount = s.position_count || 0;
    const maxPos = s.max_positions || 5;
    const pct = Math.min(100, (posCount / maxPos) * 100);
    bar.style.width = pct + '%';
    bar.className = 'fill' + (pct >= 80 ? ' warn' : '') + (pct >= 100 ? ' danger' : '');

  } catch(e) {
    document.getElementById('dot').className = 'status-dot offline';
    document.getElementById('status-text').textContent = 'Offline — engine not running';
  }
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>"""


# ── API handler ─────────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self._serve_html()
        elif self.path == '/api/state':
            self._serve_api()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_html(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(PAGE.encode())

    def _serve_api(self):
        """Serve the current engine state as JSON for the dashboard JS."""
        state = self._read_state()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(state, default=str).encode())

    def _read_state(self) -> dict:
        """Read engine state + trade log and combine into one API response."""
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "equity": 100_000,
            "position_count": 0,
            "trade_count": 0,
            "paused": False,
            "pause_reason": "",
            "positions": {},
            "recent_trades": [],
            "regime": "unknown",
            "layer1": {"approved": 0},
            "layer2": {"signals_above": 0},
            "layer3": {"exits": 0},
            "max_positions": 5,
        }

        # Read state file
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    s = json.load(f)
                result["timestamp"] = s.get("timestamp", result["timestamp"])
                result["equity"] = s.get("equity", result["equity"])
                result["trade_count"] = s.get("trade_count", 0)
                result["positions"] = s.get("positions", {})
                result["position_count"] = len(result["positions"])
            except (json.JSONDecodeError, OSError):
                pass

        # Smoke test state fallback
        smoke_path = Path("/tmp/eq_smoke_state.json")
        if smoke_path.exists():
            try:
                with open(smoke_path) as f:
                    s = json.load(f)
                if not result["positions"]:
                    result["positions"] = s.get("positions", {})
                    result["position_count"] = len(result["positions"])
                result["equity"] = s.get("equity", result["equity"])
                result["trade_count"] = s.get("trade_count", result["trade_count"])
            except (json.JSONDecodeError, OSError):
                pass

        # Read trade log
        if TRADES_LOG.exists():
            try:
                with open(TRADES_LOG) as f:
                    lines = f.readlines()
                trades = []
                for line in lines[-50:]:
                    line = line.strip()
                    if line:
                        try:
                            trades.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                result["recent_trades"] = trades
            except OSError:
                pass

        return result

    def log_message(self, format, *args):
        pass  # suppress access logs


def main():
    parser = argparse.ArgumentParser(description="Equity Engine Web Dashboard")
    parser.add_argument("--port", type=int, default=8080,
                        help="Port to listen on (default: 8080)")
    args = parser.parse_args()

    server = HTTPServer(("0.0.0.0", args.port), DashboardHandler)
    print(f"\n{'=' * 60}")
    print(f"  📊 Equity Engine Dashboard")
    print(f"  Open: http://localhost:{args.port}")
    print(f"  Press Ctrl+C to stop")
    print(f"{'=' * 60}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()


if __name__ == "__main__":
    main()