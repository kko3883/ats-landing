#!/usr/bin/env python3
"""FX Position Check — run from terminal to see positions + chart"""
import yfinance as yf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
import json, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HKT = timezone(timedelta(hours=8))
BASE = Path.home() / '.hermes' / 'trading'
STATE_FILE = BASE / 'fx_state.json'
OUT_FILE = Path.home() / '.hermes' / 'image_cache' / 'fx_positions_chart.png'

state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
positions = state.get('positions', {})

if not positions:
    print("No open positions.")
    sys.exit(0)

pair_ticker = {'EUR/USD': 'EURUSD=X', 'NZD/JPY': 'NZDJPY=X', 'AUD/JPY': 'AUDJPY=X', 'GBP/USD': 'GBPUSD=X'}
dfs = {}
for pair, ticker in pair_ticker.items():
    df = yf.download(ticker, interval='1m', period='5d', progress=False)
    if df is not None and not df.empty:
        dfs[pair] = df

def cseries(df):
    c = df['Close']
    return c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c

def hseries(df):
    h = df['High']
    return h.iloc[:, 0] if isinstance(h, pd.DataFrame) else h

def lseries(df):
    l = df['Low']
    return l.iloc[:, 0] if isinstance(l, pd.DataFrame) else l

# ── Text summary ──
print(f"\n{'='*50}")
print(f"  FX Position Report — {datetime.now(HKT).strftime('%b %d, %H:%M HKT')}")
print(f"{'='*50}")

for pair, pos in positions.items():
    d = pos['direction']
    sz = f"{pos['size']:,} units" if pos['size'] >= 1000 else f"{pos['size']} units"
    entry = pos['entry_price']
    stop = pos['stop_price']
    highest = pos.get('highest_price', entry)

    df = dfs.get(pair)
    if df is not None and not df.empty:
        current = float(cseries(df).iloc[-1])
        pnl = round((current - entry) / entry * 100, 3)
        dist = abs(current - stop)
        pips = dist * 100 if 'JPY' in pair else dist * 10000
        print(f"\n  {pair} {d} — {sz}")
        print(f"  Entry: {entry}")
        print(f"  Stop:  {stop} (trailed)")
        print(f"  Peak:  {highest}")
        print(f"  Now:   {current}  |  PnL: {pnl:+.3f}%  |  Stop {dist:.4f} ({pips:.1f} pips)")
    else:
        print(f"\n  {pair} {d} — {sz} (no price data)")

print(f"\n{'='*50}\n")

# ── Chart ──
n = len(positions)
if n == 0:
    sys.exit(0)

fig, axes = plt.subplots(n, 1, figsize=(14, 5 * n))
if n == 1:
    axes = [axes]

fig.patch.set_facecolor('#1a1a2e')
CARD = '#16213e'
GREEN = '#00d4aa'
RED = '#ff6b6b'
BLUE = '#0fbcf9'
YELLOW = '#ffd32a'
PURPLE = '#a29bfe'
TEXT = '#e8e8e8'
GRID = '#2a2a4a'

for idx, (pair, pos) in enumerate(positions.items()):
    ax = axes[idx]
    ax.set_facecolor(CARD)
    ax.tick_params(colors=TEXT, labelsize=9)
    ax.grid(True, alpha=0.15, color=GRID)
    for s in ['top', 'right']:
        ax.spines[s].set_visible(False)
    for s in ['left', 'bottom']:
        ax.spines[s].set_color(GRID)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=HKT))
    ax.xaxis.set_major_locator(mdates.HourLocator())

    df = dfs.get(pair)
    if df is None or df.empty:
        ax.text(0.5, 0.5, 'No price data', color=TEXT, ha='center', va='center',
                fontsize=12, transform=ax.transAxes)
        ax.set_title(f'{pair} {pos["direction"]}', color=TEXT, fontsize=12)
        continue

    entry_p = pos['entry_price']
    stop_p = pos['stop_price']
    highest = pos.get('highest_price', entry_p)
    sz_info = f"{pos['size']:,} units"

    # Parse entry time
    entry_t = pos['entry_time']
    try:
        entry_t = datetime.fromisoformat(entry_t.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        entry_t = datetime.now(HKT)
    if entry_t.tzinfo is None:
        entry_t = entry_t.replace(tzinfo=HKT)

    # Plot only data since entry for clean view of current position
    window_start = pd.Timestamp(entry_t)
    c = cseries(df)
    h = hseries(df)
    lo = lseries(df)
    times = c.index

    mask = times >= window_start
    if mask.sum() < 3:
        # Not enough post-entry data yet
        ax.text(0.5, 0.5, 'Position just entered — insufficient price history yet', 
                color=TEXT, ha='center', va='center', fontsize=11,
                transform=ax.transAxes,
                bbox=dict(boxstyle='round,pad=0.5', facecolor=CARD, edgecolor=YELLOW, alpha=0.8))
        # Still draw entry/stop lines for reference
        ax.axhline(y=entry_p, color=GREEN, ls='--', lw=1, alpha=0.8)
        ax.axhline(y=stop_p, color=RED, ls=':', lw=1.2, alpha=0.8)
        ax.set_title(f'{pair} {pos["direction"]} — {sz_info} | PnL: 0.000% | Stop: {stop_p}',
                     color=TEXT, fontsize=12, pad=10)
        continue

    cf = c[mask].values.astype(float)
    hf = h[mask].values.astype(float)
    lf = lo[mask].values.astype(float)
    tf = c[mask].index

    # Price line (use HIGH for visibility of peak movement)
    # Close loses intra-candle action; high shows the full range
    ax.plot(tf, hf, color=BLUE, lw=1.5, label='Price (high)')
    ax.fill_between(tf, lf, hf, color=BLUE, alpha=0.06, label='High-Low range')

    # Entry line
    ax.axhline(y=entry_p, color=GREEN, ls='--', lw=1, alpha=0.8)

    # Entry marker
    entry_mask = tf >= pd.Timestamp(entry_t)
    if entry_mask.any():
        ei = int(entry_mask.argmax())
        ax.scatter(tf[ei], cf[ei], color=GREEN, s=140, zorder=7, marker='^',
                   edgecolors='white', linewidth=1)
        ax.annotate(f'ENTRY {entry_p}', (tf[ei], cf[ei]),
                    fontsize=9, color=GREEN, fontweight='bold',
                    xytext=(10, -22), textcoords='offset points',
                    bbox=dict(boxstyle='round,pad=0.3', facecolor=CARD, edgecolor=GREEN, alpha=0.9))

    # Peak annotation (only if significantly above entry)
    if highest > entry_p * 1.00005:
        ax.axhline(y=highest, color=PURPLE, ls='-.', lw=1, alpha=0.6)
        # Place peak label at the right edge of the chart
        ax.annotate(f'PEAK {highest:.5f}', xy=(1, highest), fontsize=9, color=PURPLE,
                    fontweight='bold', ha='right', va='bottom', xycoords=('axes fraction', 'data'),
                    bbox=dict(boxstyle='round,pad=0.3', facecolor=CARD, edgecolor=PURPLE, alpha=0.9))

    # Stop line
    ax.axhline(y=stop_p, color=RED, ls=':', lw=1.2, alpha=0.8)
    ax.annotate(f'STOP {stop_p}', xy=(1, stop_p), fontsize=9, color=RED,
                ha='right', va='top', xycoords=('axes fraction', 'data'),
                bbox=dict(boxstyle='round,pad=0.3', facecolor=CARD, edgecolor=RED, alpha=0.9))

    # Current price
    ax.scatter(tf[-1], cf[-1], color=YELLOW, s=120, zorder=7, marker='o',
               edgecolors='white', linewidth=1)
    ax.annotate(f'{cf[-1]:.5f}', (tf[-1], cf[-1]),
                fontsize=11, color=YELLOW, fontweight='bold',
                xytext=(10, -10), textcoords='offset points')

    # Fill profit/loss zones
    if entry_mask.any():
        ei = int(entry_mask.argmax())
        after_cf = cf[ei:]
        after_tf = tf[ei:]
        ax.fill_between(after_tf, entry_p, after_cf,
                         where=after_cf >= entry_p, color=GREEN, alpha=0.07)
        ax.fill_between(after_tf, entry_p, after_cf,
                         where=after_cf < entry_p, color=RED, alpha=0.07)

    # Title
    current_v = float(cf[-1])
    pnl_pct = round((current_v - entry_p) / entry_p * 100, 3)
    ax.set_title(f'{pair} {pos["direction"]} — {sz_info} | PnL: {pnl_pct:+.3f}% | Stop: {stop_p}',
                 color=TEXT, fontsize=12, pad=10)

    # Time range
    ax.text(0.98, 0.02, f'{tf[0].strftime("%m/%d %H:%M")} - {tf[-1].strftime("%H:%M")} HKT',
            transform=ax.transAxes, fontsize=7, color=TEXT, ha='right', va='bottom', alpha=0.5)

    # Legend
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color=BLUE, lw=1.5, label='Price (close)'),
        Line2D([0], [0], color=GREEN, ls='--', lw=1, label=f'Entry {entry_p}'),
        Line2D([0], [0], color=PURPLE, ls='-.', lw=1, label=f'Peak {highest:.5f}'),
        Line2D([0], [0], color=RED, ls=':', lw=1.2, label=f'Stop {stop_p}'),
    ]
    ax.legend(handles=legend_elements, loc='upper left', fontsize=8,
              facecolor=CARD, edgecolor=GRID, labelcolor=TEXT)

    # Set y-axis to focus on the trade range
    price_min = min(lf.min(), stop_p) - 0.002
    price_max = max(hf.max(), highest, entry_p) + 0.002
    ax.set_ylim(price_min, price_max)

plt.tight_layout(pad=2)
OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(OUT_FILE, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
plt.close()
print(f"Chart saved: {OUT_FILE}")
