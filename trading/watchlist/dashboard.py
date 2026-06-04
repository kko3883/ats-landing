#!/usr/bin/env python3
"""
Generate a standalone HTML dashboard from watchlist + signals JSON.
Output: ~/.hermes/trading/dashboard.html — open in any browser.
"""

import json
from pathlib import Path

WATCHLIST_FILE = Path.home() / ".hermes" / "trading" / "watchlist.json"
SIGNALS_FILE = Path.home() / ".hermes" / "trading" / ".cache" / "signals.json"
OUTPUT_FILE = Path.home() / ".hermes" / "trading" / "dashboard.html"


def load_json(path):
    if path.exists():
        return json.loads(path.read_text())
    return None


def generate():
    wl = load_json(WATCHLIST_FILE) or {}
    sig = load_json(SIGNALS_FILE) or {}

    us_data = wl.get("us", {})
    groups = us_data.get("groups", {})
    signals_list = sig.get("signals", [])
    entry_signals = [s for s in signals_list if "enter" in s.get("action", "")]
    exit_signals = [s for s in signals_list if "exit" in s.get("action", "")]
    generated = wl.get("generated_at", "")

    # Build HTML
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ATS Watchlist Dashboard</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
          background:#0d1117; color:#c9d1d9; padding:24px; }}
  h1 {{ font-size:24px; margin-bottom:4px; color:#f0f6fc; }}
  .subtitle {{ color:#8b949e; margin-bottom:24px; font-size:13px; }}
  .stats {{ display:flex; gap:12px; margin-bottom:24px; flex-wrap:wrap; }}
  .stat {{ background:#161b22; border:1px solid #30363d; border-radius:8px;
           padding:16px 20px; min-width:120px; }}
  .stat .num {{ font-size:28px; font-weight:600; color:#58a6ff; }}
  .stat .num.green {{ color:#3fb950; }}
  .stat .num.red {{ color:#f85149; }}
  .stat .num.yellow {{ color:#d29922; }}
  .stat .label {{ font-size:11px; color:#8b949e; text-transform:uppercase; }}

  h2 {{ font-size:18px; margin:24px 0 12px; color:#f0f6fc;
        padding-bottom:8px; border-bottom:1px solid #30363d; }}

  .group-card {{ background:#161b22; border:1px solid #30363d; border-radius:8px;
                 margin-bottom:12px; overflow:hidden; }}
  .group-header {{ padding:12px 16px; display:flex; justify-content:space-between;
                   align-items:center; cursor:pointer; }}
  .group-header:hover {{ background:#1c2128; }}
  .group-name {{ font-weight:600; font-size:14px; }}
  .group-meta {{ font-size:12px; color:#8b949e; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ text-align:left; padding:8px 16px; color:#8b949e; font-weight:500;
        border-bottom:1px solid #21262d; font-size:11px; text-transform:uppercase; }}
  td {{ padding:8px 16px; border-bottom:1px solid #21262d; }}
  tr:last-child td {{ border-bottom:none; }}
  .cell-score {{ font-family:'SF Mono','Fira Code',monospace; font-size:12px; }}
  .tag {{ display:inline-block; padding:1px 6px; border-radius:4px; font-size:11px;
          font-weight:500; }}
  .tag.long {{ background:#0d5320; color:#7ee787; }}
  .tag.short {{ background:#7e0f19; color:#ffa198; }}
  .tag.entry {{ background:#0a2e6e; color:#79c0ff; }}
  .tag.exit {{ background:#4d0d15; color:#ff7b72; }}
  .status {{ margin-top:24px; padding:12px; border-radius:6px; font-size:12px; }}
  .status.warn {{ background:#3d2e00; border:1px solid #5a4200; color:#d29922; }}
  .status.info {{ background:#0a2e6e; border:1px solid #1f5a99; color:#79c0ff; }}
  details summary {{ list-style:none; }}
  details summary::-webkit-details-marker {{ display:none; }}
</style>
</head>
<body>
<h1>ATS Watchlist Dashboard</h1>
<div class="subtitle">Generated: {generated[:19]}</div>

<div class="stats">
  <div class="stat">
    <div class="num">{us_data.get('liquidity_passed', 0)}</div>
    <div class="label">Passed Liquidity</div>
  </div>
  <div class="stat">
    <div class="num yellow">{len(groups)}</div>
    <div class="label">Beta Groups</div>
  </div>
  <div class="stat">
    <div class="num green">{len(entry_signals)}</div>
    <div class="label">Entry Signals</div>
  </div>
  <div class="stat">
    <div class="num red">{len(exit_signals)}</div>
    <div class="label">Exit Signals</div>
  </div>
</div>
"""

    # ── Signals Section ──
    if entry_signals:
        html += "<h2>Entry Signals</h2>"
        for s in entry_signals[:20]:
            html += f"""<div class="group-card">
              <div class="group-header">
                <div><span class="tag entry">ENTRY</span> <strong>{s['symbol']}</strong>
                {'<span class="tag long">LONG</span>' if 'long' in s.get('action','') else '<span class="tag short">SHORT</span>'}
                <span style="color:#8b949e;font-size:12px;margin-left:8px">{s.get('strategy_name','?')}</span></div>
                <div style="font-size:12px;color:#8b949e">${s.get('price',0):.2f}{'  SL: $'+str(round(s.get('stop_loss',0),2)) if s.get('stop_loss') else ''}</div>
              </div>
            </div>"""

    # ── Watchlist Groups ──
    html += "<h2>Watchlist Groups</h2>"

    GROUP_COLORS = {
        "high_beta_growth": "#7e0f19",
        "moderate_growth": "#3d2e00",
        "neutral": "#0a2e6e",
        "moderate_defensive": "#1c4d2e",
        "defensive": "#0d5320",
    }

    for gname, g in groups.items():
        color = GROUP_COLORS.get(gname, "#30363d")
        longs = g.get("long_candidates", [])
        shorts = g.get("short_candidates", [])

        html += f"""<details open>
<summary class="group-card">
  <div class="group-header">
    <div class="group-name" style="border-left:3px solid {color};padding-left:10px">
      {gname.replace('_', ' ').title()}</div>
    <div class="group-meta">{g.get('n_stocks',0)} stocks · β_vix = {g.get('avg_beta_vix',0):+.3f} · β_dxy = {g.get('avg_beta_dxy',0):+.3f}</div>
  </div>
</summary>
<div style="padding:0 16px 12px">
  <table>
    <tr><th>Symbol</th><th>β_vix</th><th>β_dxy</th><th>RS Z-Score</th><th>Signal</th></tr>"""

        all_cands = [(s, "LONG") for s in longs] + [(s, "SHORT") for s in shorts]
        for s, direction in all_cands:
            tag = f'<span class="tag long">LONG</span>' if direction == "LONG" else f'<span class="tag short">SHORT</span>'
            rs = s.get("rs_zscore", 0)
            rs_color = "#3fb950" if rs > 0 else "#f85149"
            html += f"""<tr>
              <td><strong>{s['symbol']}</strong></td>
              <td class="cell-score">{s.get('beta_vix',0):+.3f}</td>
              <td class="cell-score">{s.get('beta_dxy',0):+.3f}</td>
              <td class="cell-score" style="color:{rs_color}">{rs:+.3f}</td>
              <td>{tag}</td>
            </tr>"""

        html += "</table></div></details>"

    # ── Data Source Notice ──
    html += f"""<div class="status info">
      <strong>Data source:</strong> Yahoo Finance (yfinance) — free, covers US &amp; HK.
      Refreshes every 24h. For real-time execution, connects through IBKR Gateway separately.
    </div>
    <div class="status warn">
      <strong>Limitations:</strong> Yahoo Finance is not a production-grade data vendor.
      It has no SLA, may throttle aggressive callers, and HK data quality is lower than US.
      Suitable for daily watchlists. For sub-minute signals, use IBKR streaming API.
    </div>
"""

    html += "</body></html>"

    OUTPUT_FILE.write_text(html)
    print(f"Dashboard written to {OUTPUT_FILE}")
    print(f"Open: file://{OUTPUT_FILE}")


if __name__ == "__main__":
    generate()
