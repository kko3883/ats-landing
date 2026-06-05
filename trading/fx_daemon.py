#!/usr/bin/env python3
"""
FX Trading Daemon v2 — asyncio + ib_async
- Adaptive polling: fast when near signal, slow when calm
- Auto-executes high-confidence trades via IBKR
- Manages positions with ATR trailing stops (only move up)
- No more ib.sleep() hangs — proper async event loop
"""
import asyncio
import json, time, os, sys, signal, random
from datetime import datetime, timezone, timedelta
from pathlib import Path
import logging
import ib_async as ib
import yfinance as yf
import pandas as pd
import numpy as np

# ── Config ──
HKT = timezone(timedelta(hours=8))
BASE = Path(os.environ.get('HOME', '/Users/kelvinko')) / '.hermes' / 'trading'
STATE_FILE = BASE / 'fx_state.json'
ALERT_FILE = BASE / 'fx_alert.json'
LOG_FILE = BASE / 'fx_daemon.log'
COOLDOWN_AFTER_TRADE = 300   # 5 min cooldown between entries for same pair
MAX_POSITIONS_PER_PAIR = 2  # max 2 positions per pair (staggered entry)
YF_PERIOD = '5d'
YF_INTERVAL = '1h'
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 5.0

# Daily loss limit — kill switch
DAILY_LOSS_LIMIT_PCT = -2.0

# Pair type config
PAIR_TYPE = {
    'EUR/USD': 'trend',
    'GBP/USD': 'trend',
    'AUD/JPY': 'carry',
    'NZD/JPY': 'carry',
}

# Safety stop (wide, only triggers on catastrophe)
CARRY_SAFETY_ATR = 10.0

# Signal-based exit thresholds
CARRY_EXIT_RSI_OVERBOUGHT = 70
CARRY_EXIT_RSI_OVERSOLD = 30

# Per-pair position sizes (units)
POSITION_SIZES = {
    'EUR/USD': 100_000,      # 1.0 lot
    'GBP/USD': 50_000,       # 0.5 lot
    'AUD/JPY': 250_000,      # 2.5 lots
    'NZD/JPY': 250_000,      # 2.5 lots
}

# Adaptive polling config
BASE_INTERVAL = 120     # seconds when calm (2 min)
WARM_INTERVAL = 30      # seconds when approaching
HOT_INTERVAL = 10       # seconds when near trigger
CRITICAL_INTERVAL = 5   # seconds at the doorstep
PROXIMITY_WARM = 20     # proximity score threshold for warm
PROXIMITY_HOT = 50      # proximity score threshold for hot
PROXIMITY_CRITICAL = 80 # proximity score threshold for critical

# 4-Level System Labels
LEVEL_LABELS = {
    -4: 'Bear exhaustion',
    -3: 'Strong bear',
    -2: 'Bear trend',
    -1: 'Early bear',
    0: 'No trend',
    1: 'Early bull',
    2: 'Bull trend',
    3: 'Strong bull',
    4: 'Bull exhaustion',
}

BASE.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
    force=True,
)
log = logging.getLogger('fx_daemon')


# ── Helpers ──
def now_hkt():
    return datetime.now(HKT)

def ts():
    return now_hkt().isoformat()

def load_json(path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return default or {}
    return default or {}

def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, default=str))


# ── Data Fetch ──
# yfinance is synchronous — wrapped with timeout via ThreadPoolExecutor
# IBKR paper has no FX market data subscription, so reqHistoricalData won't work

def fetch_fx(ticker: str) -> pd.DataFrame | None:
    """Fetch 1h OHLCV bars via yfinance with 15s timeout."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    def _fetch():
        df = yf.download(ticker, period=YF_PERIOD, interval=YF_INTERVAL, progress=False)
        if df.empty or len(df) < 60:
            log.warning(f"Not enough yfinance bars for {ticker}: {len(df) if not df.empty else 0}")
            return None
        return df
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_fetch)
            try:
                df = future.result(timeout=15)
                if df is not None:
                    log.info(f"📥 Fetched {len(df)} 1h bars for {ticker}")
                return df
            except FuturesTimeout:
                log.warning(f"⏱️ yfinance timed out for {ticker} (>15s)")
                return None
    except Exception as e:
        log.warning(f"yfinance data error for {ticker}: {e}")
        return None

def fetch_current_price(ticker: str) -> float | None:
    """Fetch latest real-time price via yfinance 5m bars with 8s timeout.
    Falls back to 1d/1m if 5m fails, then 1h bar close as last resort."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    def _fetch():
        # Try 5m bars first (gives ~~15s delay) — works for most FX pairs
        df = yf.download(ticker, period='1d', interval='5m', progress=False)
        if df.empty:
            # Fallback to 1m bars from last day
            df = yf.download(ticker, period='1d', interval='1m', progress=False)
            if df.empty:
                return None
        c = df['Close']
        close = c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c
        return float(close.iloc[-1])
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_fetch)
            price = future.result(timeout=8)
            if price is not None:
                log.info(f"💰 Current price for {ticker}: {price}")
            return price
    except FuturesTimeout:
        log.warning(f"⏱️ Current price timed out for {ticker} (>8s)")
        return None
    except Exception as e:
        log.debug(f"Current price fetch error for {ticker}: {e}")
        return None

def close_series(df: pd.DataFrame) -> pd.Series:
    c = df['Close']
    return c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c

def high_series(df: pd.DataFrame) -> pd.Series:
    h = df['High']
    return h.iloc[:, 0] if isinstance(h, pd.DataFrame) else h

def low_series(df: pd.DataFrame) -> pd.Series:
    l = df['Low']
    return l.iloc[:, 0] if isinstance(l, pd.DataFrame) else l


# ── ATR Calculation ──
def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float | None:
    if df is None or len(df) < period + 5:
        return None
    high = high_series(df)
    low = low_series(df)
    close = close_series(df)
    tr = pd.DataFrame({
        'hl': high - low,
        'hc': (high - close.shift()).abs(),
        'lc': (low - close.shift()).abs(),
    }).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr)

def calculate_atr_for_df(df_dict: dict[str, pd.DataFrame], pair: str) -> float | None:
    ticker_map = {
        'EUR/USD': 'EURUSD=X',
        'AUD/JPY': 'AUDJPY=X',
        'NZD/JPY': 'NZDJPY=X',
    }
    ticker = ticker_map.get(pair)
    if not ticker or ticker not in df_dict:
        return None
    return calculate_atr(df_dict[ticker])


# ── Unified 4-Level Signal System ──
# Level  4 = Exhaustion (trend strong + RSI extreme) → DO NOT enter, exit
# Level  3 = Strong momentum (SMA + EMA aligned) → enter on shallow pullback
# Level  2 = Trend established (SMA structure) → enter on pullback
# Level  1 = Early trend (SMA golden cross) → fragile, watch only
# Level  0 = No trend → HOLD
# Level -1 = Early bear (SMA death cross) → fragile
# Level -2 = Bear trend established → enter short on rally
# Level -3 = Strong bear momentum → enter short on shallow rally
# Level -4 = Exhaustion bearish (RSI oversold) → DO NOT enter, exit

def compute_rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    return float((100 - 100 / (1 + gain / loss)).iloc[-1])

def compute_level(close: pd.Series, rsi: float) -> int:
    """Return level -4 to +4 based on SMA structure → EMA momentum → RSI exhaustion."""
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()

    current = float(close.iloc[-1])
    s20 = float(sma20.iloc[-1])
    s50 = float(sma50.iloc[-1])
    e20 = float(ema20.iloc[-1])
    e50 = float(ema50.iloc[-1])
    s20_prev = float(sma20.iloc[-2])
    s50_prev = float(sma50.iloc[-2])

    trend_up = current > s20 and s20 > s50
    trend_down = current < s20 and s20 < s50
    momentum_up = current > e20 and e20 > e50
    momentum_down = current < e20 and e20 < e50
    sma_golden = s20_prev <= s50_prev and s20 > s50
    sma_death = s20_prev >= s50_prev and s20 < s50

    # Ascending priority: exhaustion trumps everything
    if trend_down and momentum_down and rsi < 30:
        return -4   # oversold + bear → exhausted
    elif trend_up and momentum_up and rsi > 70:
        return 4    # overbought + bull → exhausted
    elif momentum_down:
        return -3   # EMA bear → strong bear momentum
    elif momentum_up:
        return 3    # EMA bull → strong bull momentum
    elif trend_down:
        return -2   # SMA bear → bear established
    elif trend_up:
        return 2    # SMA bull → bull established
    elif sma_death:
        return -1   # death cross → early bear
    elif sma_golden:
        return 1    # golden cross → early bull
    else:
        return 0    # no clear structure

def analyze_pair(ticker: str, df: pd.DataFrame) -> dict | None:
    """Unified analysis for ANY FX pair using the 4-level system."""
    if df is None or len(df) < 50:
        return None

    name = ticker.replace('=X', '')
    readable = f"{name[:3]}/{name[3:]}"

    close = close_series(df)
    current = float(close.iloc[-1])

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()

    s20 = float(sma20.iloc[-1])
    s50 = float(sma50.iloc[-1])
    e20 = float(ema20.iloc[-1])
    e50 = float(ema50.iloc[-1])

    rsi = compute_rsi(close)
    level = compute_level(close, rsi)

    trend_up = current > s20 and s20 > s50
    momentum_up = current > e20 and e20 > e50

    sma_golden = float(sma20.iloc[-2]) <= float(sma50.iloc[-2]) and s20 > s50
    sma_death = float(sma20.iloc[-2]) >= float(sma50.iloc[-2]) and s20 < s50

    # Entry signal based on level + RSI
    signal = 'HOLD'
    confidence = 0

    if level >= 2 and rsi < 40:
        signal = 'LONG'
        confidence = min(level, 3)
    elif level <= -2 and rsi > 55:
        signal = 'SHORT'
        confidence = min(abs(level), 3)
    # Level 3+ allows slightly higher RSI for pullback entries
    if level == 3 and 40 <= rsi < 50 and signal == 'HOLD':
        signal = 'LONG'
        confidence = 3
    elif level == -3 and 50 >= rsi > 45 and signal == 'HOLD':
        signal = 'SHORT'
        confidence = 3

    return {
        'pair': readable,
        'price': round(current, 5),
        'signal': signal,
        'confidence': confidence,
        'level': level,
        'rsi': round(rsi, 1),
        'trend_up': trend_up,
        'momentum_up': momentum_up,
        'sma20_delta': round(current - s20, 5),
        'sma50_delta': round(current - s50, 5),
        'ema20_delta': round(current - e20, 5),
        'ema50_delta': round(current - e50, 5),
        'sma_golden': sma_golden,
        'sma_death': sma_death,
        'timestamp': ts(),
    }


# ── Adaptive Proximity ──
def calculate_proximity(signals: list[dict], positions: dict) -> int:
    """
    Return 0-100: how close any pair is to triggering a trade.
    0   = far from any threshold (30s poll)
    100 = at the doorstep (2s poll)

    Uses the level system: level 1/2 thresholds are proximity targets.
    """
    proximity = 0

    for sig in signals:
        rsi = sig['rsi']
        level = sig.get('level', 0)

        if sig['signal'] != 'HOLD':
            continue

        # LONG proximity: approaching level 2 + RSI < 40
        if level == 1 and rsi < 45:
            proximity = max(proximity, 40)
        if level == 1 and rsi < 40:
            proximity = max(proximity, 70)

        if sig.get('trend_up'):
            if 35 <= rsi <= 45:
                proximity = max(proximity, 40)
            if 35 <= rsi <= 42:
                proximity = max(proximity, 70)
            if rsi < 35:
                proximity = max(proximity, 90)

        if sig.get('momentum_up'):
            if 40 <= rsi <= 55:
                proximity = max(proximity, 50)
            if 40 <= rsi <= 50:
                proximity = max(proximity, 75)

        # SHORT proximity
        if level == -1 and rsi > 50:
            proximity = max(proximity, 40)
        if level == -1 and rsi > 55:
            proximity = max(proximity, 70)

        if not sig.get('trend_up') and level < 0:
            if 50 <= rsi <= 60:
                proximity = max(proximity, 40)
            if 53 <= rsi <= 57:
                proximity = max(proximity, 70)
            if rsi > 57:
                proximity = max(proximity, 90)

    if positions:
        proximity = max(proximity, 30)

    return min(proximity, 100)


def get_poll_interval(proximity: int) -> int:
    """Map proximity (0-100) to polling interval in seconds."""
    if proximity >= PROXIMITY_CRITICAL:
        return CRITICAL_INTERVAL
    if proximity >= PROXIMITY_HOT:
        return HOT_INTERVAL
    if proximity >= PROXIMITY_WARM:
        return WARM_INTERVAL
    return BASE_INTERVAL


# ── Alert / Formatting ──
def format_entry_alert(data: dict, trade_result: dict | None = None) -> str:
    lines = []
    pair = data['pair']
    level = data.get('level', 0)
    level_label = LEVEL_LABELS.get(level, f"L{level}")
    direction = '📈 LONG' if data['signal'] == 'LONG' else '📉 SHORT'

    stop_info = ""
    if trade_result and trade_result.get('stop_price'):
        stop_info = f" | Stop: {trade_result['stop_price']}"

    lines.append(f"🚨 Signal — {pair}")
    lines.append(f"{direction} (level {level}: {level_label}, conf: {data['confidence']})")
    lines.append(f"Price: {data['price']}{stop_info}")
    lines.append(f"RSI: {data['rsi']}")

    if data.get('trend_up'):
        lines.append(f"Trend: ▲ SMA20 > SMA50")
    else:
        lines.append(f"Trend: ▼ SMA20 < SMA50" if level < 0 else f"Trend: — SMA crossing")

    if data.get('momentum_up'):
        lines.append(f"Momentum: ▲ EMA20 > EMA50")
    elif level < 0:
        lines.append(f"Momentum: ▼ EMA20 < EMA50")

    sd20 = data.get('sma20_delta', '?')
    sd50 = data.get('sma50_delta', '?')
    ed20 = data.get('ema20_delta', '?')
    ed50 = data.get('ema50_delta', '?')
    lines.append(f"SMA20: {sd20:+.5f} | SMA50: {sd50:+.5f}")
    lines.append(f"EMA20: {ed20:+.5f} | EMA50: {ed50:+.5f}")

    if trade_result:
        if 'error' in trade_result:
            lines.append(f"Execution error: {trade_result['error']}")
        elif 'skipped' in trade_result:
            lines.append(f"Skipped: {trade_result['reason']}")
        else:
            lines.append(f"Order: {trade_result['action']} {trade_result['size']} @ ~{trade_result['price']} ({trade_result['status']})")

    return '\n'.join(lines)


def format_exit_alert(data: dict) -> str:
    lines = []
    lines.append(f"🚨 Stop Hit — {data.get('pair', '?')}")
    lines.append(f"Closed {data.get('direction', '?')} @ {data.get('price', '?')}")
    if data.get('pnl'):
        lines.append(f"PnL: {data['pnl']}")
    if data.get('reason'):
        lines.append(f"Reason: {data['reason']}")
    return '\n'.join(lines)


def check_for_alerts(old_state: dict, new_signals: list[dict]) -> list[dict]:
    """Compare new signals to old state and return alerts for new entries."""
    alerts = []
    for sig in new_signals:
        if sig.get('confidence', 0) < 2 or sig['signal'] == 'HOLD':
            continue
        pair = sig['pair']
        old_sig = 'HOLD'
        old_eur = old_state.get('eur_usd', {})
        old_carry_list = old_state.get('carry', [])
        if old_eur.get('pair') == pair:
            old_sig = old_eur.get('signal', 'HOLD')
        else:
            for oc in old_carry_list:
                if oc.get('pair') == pair:
                    old_sig = oc.get('signal', 'HOLD')
                    break
        if sig['signal'] != old_sig:
            alerts.append({
                'type': 'entry',
                'pair': pair,
                'data': sig,
                'message': format_entry_alert(sig),
            })
    return alerts


# ═══════════════════════════════════════════
# ASYNC IBKR LAYER — ib_async based
# ═══════════════════════════════════════════

_ib = None
_ib_client_id = 1  # persistent clientId; changed only on reconnect-after-conflict
TRADE_COOLDOWN = {}

FX_CONTRACT_MAP = {
    'EUR/USD': ('EURUSD', 'EUR.USD'),
    'AUD/JPY': ('AUDJPY', 'AUD.JPY'),
    'NZD/JPY': ('NZDJPY', 'NZD.JPY'),
}

def _get_fx_symbol(pair: str) -> str | None:
    entry = FX_CONTRACT_MAP.get(pair)
    return entry[0] if entry else None

def _get_local_symbol(pair: str) -> str | None:
    entry = FX_CONTRACT_MAP.get(pair)
    return entry[1] if entry else None


async def ensure_ib_async():
    """Async IBKR connection manager with short timeout. Returns ib instance or None.
    If IBKR server is unreachable (Error 1100), returns None gracefully so the
    daemon can continue in data-only mode with yfinance."""
    global _ib, _ib_client_id
    try:
        if _ib and _ib.isConnected():
            # Fast ping with 3s timeout — if IBKR server is down, don't block
            try:
                await asyncio.wait_for(_ib.reqCurrentTimeAsync(), timeout=3)
                return _ib
            except asyncio.TimeoutError:
                log.debug("IBKR ping timed out — reconnecting")
                _ib.isConnected = lambda: False  # force reconnect
            except Exception:
                pass
    except:
        pass
    try:
        if _ib:
            try:
                _ib.disconnect()
            except:
                pass
        _ib = ib.IB()
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(
            _ib.connectAsync('127.0.0.1', 4002, clientId=_ib_client_id),
            timeout=5
        )
        log.info(f"🔗 Async IBKR connection established (clientId={_ib_client_id})")
        return _ib
    except asyncio.TimeoutError:
        old_id = _ib_client_id
        _ib_client_id = random.randint(2, 9999)
        log.warning(f"⏱️ IBKR connect timed out (clientId={old_id} likely stale) → retrying with {_ib_client_id}")
        try:
            if _ib:
                try:
                    _ib.disconnect()
                except:
                    pass
            _ib = ib.IB()
            await asyncio.wait_for(
                _ib.connectAsync('127.0.0.1', 4002, clientId=_ib_client_id),
                timeout=5
            )
            log.info(f"🔗 Reconnected with new clientId={_ib_client_id}")
            return _ib
        except asyncio.TimeoutError:
            log.warning("⏱️ IBKR retry also timed out — running data-only mode")
        except Exception as e2:
            log.warning(f"⚠️ IBKR retry failed: {e2}")
        return None
    except Exception as e:
        err_str = str(e)
        if '326' in err_str or 'already in use' in err_str.lower():
            old_id = _ib_client_id
            _ib_client_id = random.randint(2, 9999)
            log.warning(f"⚠️ clientId {old_id} already in use → retrying with {_ib_client_id}")
            # Retry once with new clientId
            try:
                if _ib:
                    try:
                        _ib.disconnect()
                    except:
                        pass
                _ib = ib.IB()
                await asyncio.wait_for(
                    _ib.connectAsync('127.0.0.1', 4002, clientId=_ib_client_id),
                    timeout=5
                )
                log.info(f"🔗 Reconnected with new clientId={_ib_client_id}")
                return _ib
            except Exception as e2:
                log.warning(f"⚠️ Reconnect also failed: {e2}")
        else:
            log.warning(f"⚠️ IBKR connection failed: {e}")
        return None


def disconnect_ib():
    """Disconnect the persistent IBKR connection."""
    global _ib
    if _ib:
        try:
            _ib.disconnect()
            log.info("🔌 IBKR connection closed")
        except:
            pass
    _ib = None


async def sync_positions_from_ibrk_async() -> dict:
    """Query IBKR for actual open positions via persistent async connection."""
    try:
        ib_client = await ensure_ib_async()
        if not ib_client:
            return {}

        ibrk_positions = await ib_client.reqPositionsAsync()
        result = {}

        for p in ibrk_positions:
            ls = p.contract.localSymbol
            pair_map = {v[1]: k for k, v in FX_CONTRACT_MAP.items()}
            pair = pair_map.get(ls)
            if not pair:
                continue
            size = int(p.position)
            if size == 0:
                continue
            direction = 'LONG' if size > 0 else 'SHORT'
            result[pair] = {
                'direction': direction,
                'size': abs(size),
                'entries': 1,
                'entry_prices': [float(p.avgCost) if p.avgCost else 0.0],
                'last_entry_time': time.time(),
                'entry_price': float(p.avgCost) if p.avgCost else 0.0,
                'highest_price': float(p.avgCost) if direction == 'LONG' else float(p.avgCost),
                'lowest_price': float(p.avgCost) if direction == 'SHORT' else float(p.avgCost),
                'stop_price': None,
                'stop_order_id': None,
                'entry_time': ts(),
                'pair': pair,
            }

        return result
    except Exception as e:
        log.warning(f"⚠️ Could not sync from IBKR: {e}")
        return {}


async def get_account_summary_async() -> tuple[float | None, str | None]:
    """Get NetLiquidation value from IBKR. Returns (equity, currency) or (None, None)."""
    try:
        ib_client = await ensure_ib_async()
        if not ib_client:
            return None, None
        summary = await ib_client.reqAccountSummaryAsync()
        for a in summary:
            if a.tag == 'NetLiquidation':
                return float(a.value), a.currency
        return None, None
    except Exception as e:
        log.warning(f"⚠️ Could not get account summary: {e}")
        return None, None


async def place_trade_async(pair: str, signal: str, price: float,
                            atr: float | None = None, existing_size: int = 0) -> dict:
    """Place a trade asynchronously via ib_async."""
    last_trade = TRADE_COOLDOWN.get(pair, 0)
    if time.time() - last_trade < COOLDOWN_AFTER_TRADE:
        remaining = int(COOLDOWN_AFTER_TRADE - (time.time() - last_trade))
        return {'skipped': True, 'reason': f'cooldown ({remaining}s left)', 'pair': pair}

    try:
        ib_client = await ensure_ib_async()
        if not ib_client:
            return {'error': 'IBKR not connected', 'pair': pair}

        pair_str = _get_fx_symbol(pair)
        if not pair_str:
            return {'error': f'Unknown pair: {pair}', 'pair': pair}

        contract = ib.Forex(pair_str)
        await ib_client.qualifyContractsAsync(contract)

        size = POSITION_SIZES.get(pair, 50000)
        total_size = existing_size + size

        action = 'BUY' if signal == 'LONG' else 'SELL'
        entry_order = ib.MarketOrder(action, size)
        entry_order.tif = 'DAY'
        trade = ib_client.placeOrder(contract, entry_order)

        stop_price = None
        stop_order_id = None
        if atr is not None and atr > 0:
            pair_type = PAIR_TYPE.get(pair, 'trend')
            entry_atr_mult = CARRY_SAFETY_ATR if pair_type == 'carry' else ATR_STOP_MULTIPLIER
            if signal == 'LONG':
                stop_price = round(price - (atr * entry_atr_mult), 5)
            else:
                stop_price = round(price + (atr * entry_atr_mult), 5)

            stop_action = 'SELL' if signal == 'LONG' else 'BUY'
            rounded_stop = round(stop_price, 3) if 'JPY' in pair else round(stop_price, 5)
            stop_order = ib.StopOrder(stop_action, total_size, rounded_stop)
            stop_order.tif = 'DAY'
            stop_trade = ib_client.placeOrder(contract, stop_order)
            stop_order_id = stop_trade.orderStatus.orderId
            log.info(f"🛡️  StopOrder placed on IBKR: {pair} {stop_action} {total_size} @ {stop_price} (orderId: {stop_order_id})")

        result = {
            'pair': pair,
            'action': action,
            'size': size,
            'total_size': total_size,
            'price': price,
            'atr': round(atr, 5) if atr else None,
            'stop_price': stop_price,
            'stop_order_id': stop_order_id,
            'highest_price': price if signal == 'LONG' else price,
            'lowest_price': price if signal == 'SHORT' else price,
            'status': trade.orderStatus.status,
            'order_id': trade.orderStatus.orderId,
            'timestamp': ts(),
        }

        TRADE_COOLDOWN[pair] = time.time()
        return result

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.warning(f"❌ Trade failed for {pair}: {e}\n{tb}")
        return {'error': str(e), 'traceback': tb, 'pair': pair, 'signal': signal}


async def close_position_async(pair: str, direction: str, size: int, price: float) -> dict:
    """Close a position using a short-lived IBKR connection (avoids event loop interference)."""
    try:
        # Use a separate temp connection for order execution
        temp_ib = ib.IB()
        await temp_ib.connectAsync('127.0.0.1', 4002, clientId=random.randint(50, 200))

        pair_str = _get_fx_symbol(pair)
        if not pair_str:
            temp_ib.disconnect()
            return {'error': f'Unknown pair: {pair}', 'pair': pair}

        contract = ib.Forex(pair_str)
        await temp_ib.qualifyContractsAsync(contract)

        action = 'SELL' if direction == 'LONG' else 'BUY'
        order = ib.MarketOrder(action, size)
        order.tif = 'DAY'
        trade = temp_ib.placeOrder(contract, order)
        await asyncio.sleep(0.5)  # brief wait for fill

        result = {
            'pair': pair,
            'action': action,
            'size': size,
            'price': price,
            'reason': f'{direction}_signal_exit',
            'status': trade.orderStatus.status,
            'order_id': trade.orderStatus.orderId,
            'filled': trade.orderStatus.filled,
            'avg_fill': trade.orderStatus.avgFillPrice,
            'timestamp': ts(),
        }
        temp_ib.disconnect()
        return result
    except Exception as e:
        return {'error': str(e), 'pair': pair}


async def _update_ibkr_stop_async(pair: str, direction: str, size: int, new_stop: float,
                                  old_stop_order_id: int, updated_positions: dict):
    """Place a new StopOrder on IBKR and cancel the old one."""
    try:
        ib_client = await ensure_ib_async()
        if not ib_client:
            return
        pair_str = _get_fx_symbol(pair)
        if not pair_str:
            return
        contract = ib.Forex(pair_str)
        await ib_client.qualifyContractsAsync(contract)
        stop_action = 'SELL' if direction == 'LONG' else 'BUY'
        rounded_s = round(new_stop, 3) if 'JPY' in pair else round(new_stop, 5)
        new_order = ib.StopOrder(stop_action, size, rounded_s)
        new_order.tif = 'DAY'
        trade = ib_client.placeOrder(contract, new_order)
        if hasattr(trade, 'order') and hasattr(trade.order, 'orderId'):
            new_id = trade.order.orderId
        else:
            new_id = int(trade)
        try:
            ib_client.cancelOrder(old_stop_order_id)
        except Exception:
            pass  # old order might already be gone
        updated_positions[pair]['stop_order_id'] = new_id
        log.info(f"🛡️  Stop trailed: {pair} {direction} @ {rounded_s} (orderId: {new_id})")
    except Exception as e:
        log.warning(f"⚠️ Could not update IBKR stop for {pair}: {e}")


async def _close_and_cancel_async(pair: str, pos: dict, current_price: float,
                                  exits: list, updated_positions: dict, reason: str = ""):
    """Cancel stop order and close position asynchronously."""
    stop_order_id = pos.get('stop_order_id')
    if stop_order_id:
        try:
            ib_client = await ensure_ib_async()
            if ib_client:
                ib_client.cancelOrder(stop_order_id)
        except:
            pass

    exit_result = await close_position_async(pair, pos['direction'], pos['size'], current_price)
    multiplier = -1 if pos['direction'] == 'SHORT' else 1
    pnl = round(multiplier * (current_price - pos['entry_price']) / pos['entry_price'] * 100, 2)

    exit_event = {
        'type': 'exit', 'pair': pair, 'direction': pos['direction'],
        'entry_price': pos['entry_price'], 'exit_price': current_price,
        'stop_price': pos.get('stop_price'), 'pnl_pct': pnl, 'highest': pos.get('highest_price'),
        'reason': reason,
        'trade_result': exit_result,
        'message': format_exit_alert({
            'pair': pair, 'direction': pos['direction'],
            'price': current_price, 'pnl': f"{pnl:+.2f}%",
            'reason': reason.replace('_', ' ').replace('level ', 'L'),
        }),
        'timestamp': ts(),
    }
    exits.append(exit_event)
    log.info(f"🛑 STOP HIT: {pair} {pos['direction']} closed @ {current_price} (PnL: {pnl:+.2f}%)")
    if pair in updated_positions:
        del updated_positions[pair]
    TRADE_COOLDOWN[pair] = time.time()


async def manage_positions_async(state: dict, df_dict: dict[str, pd.DataFrame],
                                 carry_signals: list = None, current_prices: dict = None) -> tuple:
    """Manage open positions with signal-based exits and ATR trailing stops. Async version."""
    positions = state.get('positions', {})
    exits = []
    stop_updates = []
    updated_positions = {}

    signal_lookup = {}
    if carry_signals:
        for s in carry_signals:
            signal_lookup[s['pair']] = s

    for pair, pos in positions.items():
        updated_positions[pair] = dict(pos)
        direction = pos.get('direction')

        ticker_map = {'EUR/USD': 'EURUSD=X', 'AUD/JPY': 'AUDJPY=X', 'NZD/JPY': 'NZDJPY=X'}
        ticker = ticker_map.get(pair)
        if not ticker or ticker not in df_dict or df_dict[ticker] is None:
            continue

        df = df_dict[ticker]
        close = close_series(df)
        current_price = current_prices.get(pair) if current_prices else None
        if current_price is None:
            current_price = float(close.iloc[-1])

        # ── Signal-based exit (level system) ──
        sig = signal_lookup.get(pair, {})
        level = sig.get('level', 0)
        rsi = sig.get('rsi', 50)

        if direction == 'LONG':
            if level <= 0:
                reason = f"trend faded to level {level}"
                log.info(f"📉 Signal exit: {pair} {reason} @ {current_price}")
                await _close_and_cancel_async(pair, pos, current_price, exits, updated_positions, reason)
                continue
            if level >= 4 or rsi > 70:
                reason = f"bull exhaustion (RSI {rsi})"
                log.info(f"📈 Signal exit: {pair} {reason} @ {current_price}")
                await _close_and_cancel_async(pair, pos, current_price, exits, updated_positions, reason)
                continue

        elif direction == 'SHORT':
            if level >= 0:
                reason = f"trend recovered to level {level}"
                log.info(f"📈 Signal exit: {pair} {reason} @ {current_price}")
                await _close_and_cancel_async(pair, pos, current_price, exits, updated_positions, reason)
                continue
            if level <= -4 or rsi < 30:
                reason = f"bear exhaustion (RSI {rsi})"
                log.info(f"📉 Signal exit: {pair} {reason} @ {current_price}")
                await _close_and_cancel_async(pair, pos, current_price, exits, updated_positions, reason)
                continue

        atr_val = calculate_atr(df)
        if atr_val is None or atr_val <= 0:
            continue

        pair_type = PAIR_TYPE.get(pair, 'trend')
        atr_mult = CARRY_SAFETY_ATR if pair_type == 'carry' else ATR_STOP_MULTIPLIER

        if direction == 'LONG':
            if current_price > pos.get('highest_price', pos['entry_price']):
                updated_positions[pair]['highest_price'] = current_price

            highest = updated_positions[pair]['highest_price']
            new_stop = round(highest - (atr_val * atr_mult), 3 if 'JPY' in pair else 5)
            old_stop = pos.get('stop_price')

            if old_stop is None or new_stop > old_stop:
                updated_positions[pair]['stop_price'] = new_stop
                if old_stop is not None:
                    stop_updates.append({
                        'pair': pair, 'old_stop': old_stop, 'new_stop': new_stop,
                        'atr': round(atr_val, 5), 'highest': round(highest, 5),
                    })

                stop_order_id = pos.get('stop_order_id')
                if stop_order_id and old_stop is not None and new_stop > old_stop:
                    await _update_ibkr_stop_async(pair, direction, pos['size'], new_stop, stop_order_id, updated_positions)
                elif old_stop is None:
                    stop_action = 'SELL' if direction == 'LONG' else 'BUY'
                    try:
                        ib_client = await ensure_ib_async()
                        if ib_client:
                            pair_str = _get_fx_symbol(pair)
                            if pair_str:
                                contract = ib.Forex(pair_str)
                                await ib_client.qualifyContractsAsync(contract)
                                stop_order = ib.StopOrder(stop_action, pos['size'], new_stop)
                                stop_order.tif = 'DAY'
                                trade = ib_client.placeOrder(contract, stop_order)
                                updated_positions[pair]['stop_order_id'] = trade.orderStatus.orderId
                                log.info(f"🛡️ Initial stop placed: {pair} {stop_action} {pos['size']} @ {new_stop}")
                    except Exception as e:
                        log.warning(f"⚠️ Could not place initial stop for {pair}: {e}")

            stop = updated_positions[pair]['stop_price']
            if stop is not None and current_price <= stop:
                await _close_and_cancel_async(pair, pos, current_price, exits, updated_positions)

        elif direction == 'SHORT':
            if current_price < pos.get('lowest_price', pos['entry_price']):
                updated_positions[pair]['lowest_price'] = current_price

            lowest = updated_positions[pair].get('lowest_price', pos['entry_price'])
            new_stop = round(lowest + (atr_val * atr_mult), 3 if 'JPY' in pair else 5)
            old_stop = pos.get('stop_price')

            if old_stop is None or new_stop < old_stop:
                updated_positions[pair]['stop_price'] = new_stop
                if old_stop is not None:
                    stop_updates.append({
                        'pair': pair, 'old_stop': old_stop, 'new_stop': new_stop,
                        'atr': round(atr_val, 5), 'lowest': round(lowest, 5),
                    })

                stop_order_id = pos.get('stop_order_id')
                if stop_order_id and old_stop is not None and new_stop < old_stop:
                    await _update_ibkr_stop_async(pair, direction, pos['size'], new_stop, stop_order_id, updated_positions)
                elif old_stop is None:
                    stop_action = 'BUY' if direction == 'SHORT' else 'SELL'
                    try:
                        ib_client = await ensure_ib_async()
                        if ib_client:
                            pair_str = _get_fx_symbol(pair)
                            if pair_str:
                                contract = ib.Forex(pair_str)
                                await ib_client.qualifyContractsAsync(contract)
                                stop_order = ib.StopOrder(stop_action, pos['size'], new_stop)
                                stop_order.tif = 'DAY'
                                trade = ib_client.placeOrder(contract, stop_order)
                                updated_positions[pair]['stop_order_id'] = trade.orderStatus.orderId
                                log.info(f"🛡️ Initial stop placed: {pair} {stop_action} {pos['size']} @ {new_stop}")
                    except Exception as e:
                        log.warning(f"⚠️ Could not place initial stop for {pair}: {e}")

            stop = updated_positions[pair]['stop_price']
            if stop is not None and current_price >= stop:
                await _close_and_cancel_async(pair, pos, current_price, exits, updated_positions)

    return exits, stop_updates, updated_positions


# ═══════════════════════════════════════════
# MAIN ASYNC DAEMON LOOP
# ═══════════════════════════════════════════

running = True

def handle_signal(signum, frame):
    global running
    log.info("Shutdown signal received, exiting...")
    running = False


async def run_daemon_async():
    """Async main loop — proper event-driven architecture with no blocking sleeps."""
    global running

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # ── Daily loss limit tracking ──
    daily_start_equity = None
    daily_low_equity = None
    kill_switched = False

    log.info("=" * 50)
    log.info("FX Trading Daemon v2 — asyncio + ib_async")
    log.info(f"Adaptive polling: {BASE_INTERVAL}s (calm) → {CRITICAL_INTERVAL}s (critical)")
    log.info(f"Pairs: EUR/USD, AUD/JPY, NZD/JPY")
    log.info(f"ATR trailing stop: {ATR_STOP_MULTIPLIER}x")
    log.info(f"State file: {STATE_FILE}")
    log.info("=" * 50)

    # ── Establish persistent async IBKR connection ──
    ib_client = await ensure_ib_async()
    if not ib_client:
        log.warning("⚠️ IBKR not available — running in data-only mode (no execution)")

    old_state = load_json(STATE_FILE, {})

    # Sync actual positions from IBKR
    ibrk_positions = await sync_positions_from_ibrk_async()
    if ibrk_positions:
        log.info(f"📡 Synced {len(ibrk_positions)} position(s) from IBKR")
        for pair, p in ibrk_positions.items():
            log.info(f"   {pair}: {p['direction']} {p['size']:,}")
        old_positions = dict(old_state.get('positions', {}))
        for pair, ibrk_pos in ibrk_positions.items():
            existing = old_positions.get(pair, {})
            ibrk_pos['stop_price'] = existing.get('stop_price')
            ibrk_pos['stop_order_id'] = existing.get('stop_order_id')
            old_positions[pair] = ibrk_pos
        old_state['positions'] = old_positions
    else:
        old_state['positions'] = {}

    tick_count = 0
    signal_tick_count = 0
    current_interval = BASE_INTERVAL

    # Cached data between signal ticks
    df_dict = {}
    all_signals = []
    eur_analysis = None
    carry_analysis = []
    positions = old_state.get('positions', {})  # init from saved state

    SIGNAL_TICK_EVERY = 12  # Run signal analysis every 12 ticks (~10-12 min)

    while running:
        tick_start = time.time()
        tick_count += 1

        try:
            # ════════════════════════════════════════════
            # A. SIGNAL ANALYSIS — 1h bars (changes ~hourly)
            # ════════════════════════════════════════════
            if tick_count % SIGNAL_TICK_EVERY == 1 or not df_dict:
                signal_tick_count += 1
                log.info(f"📊 Signal tick #{signal_tick_count} — fetching 1h bars (every {SIGNAL_TICK_EVERY} ticks)")

                eur_df = fetch_fx('EURUSD=X')
                aud_df = fetch_fx('AUDJPY=X')
                nzd_df = fetch_fx('NZDJPY=X')
                df_dict = {'EURUSD=X': eur_df, 'AUDJPY=X': aud_df, 'NZDJPY=X': nzd_df}

                # Analyse all pairs
                all_signals = []
                for ticker, df in df_dict.items():
                    if df is not None:
                        sig = analyze_pair(ticker, df)
                        if sig:
                            all_signals.append(sig)

                eur_analysis = None
                carry_analysis = []
                for sig in all_signals:
                    if sig['pair'] == 'EUR/USD':
                        eur_analysis = sig
                    else:
                        carry_analysis.append(sig)

                # Check for NEW entry signals (only when 1h bars are fresh)
                alerts = check_for_alerts(old_state, all_signals)
                if alerts:
                    for alert in alerts:
                        log.info(f"🚨 {alert['message']}")
                        if alert['data'].get('confidence', 0) >= 2 and not kill_switched:
                            pair = alert['data']['pair']
                            atr_val = calculate_atr_for_df(df_dict, pair)
                            existing_pos = positions.get(pair, {})
                            existing_dir = existing_pos.get('direction')
                            new_dir = alert['data']['signal']
                            existing_size = existing_pos.get('size', 0)
                            entries = existing_pos.get('entries', 0)
                            last_entry = existing_pos.get('last_entry_time')

                            if existing_pos and existing_dir != new_dir:
                                log.info(f"⏭️ Skipping {pair} {new_dir} — opposite {existing_dir} exists")
                                trade_result = {'skipped': True, 'reason': f'opposite {existing_dir} exists', 'pair': pair}
                            elif existing_pos and entries >= MAX_POSITIONS_PER_PAIR:
                                log.info(f"⏭️ Skipping {pair} {new_dir} — already at max {MAX_POSITIONS_PER_PAIR} entries")
                                trade_result = {'skipped': True, 'reason': f'max {MAX_POSITIONS_PER_PAIR} entries', 'pair': pair}
                            elif existing_pos and last_entry:
                                gap = (time.time() - last_entry) if isinstance(last_entry, (int, float)) else 9999
                                if gap < COOLDOWN_AFTER_TRADE:
                                    log.info(f"⏭️ Skipping {pair} {new_dir} — only {gap:.0f}s since last entry")
                                    trade_result = {'skipped': True, 'reason': f'cooldown {gap:.0f}s/{COOLDOWN_AFTER_TRADE}s', 'pair': pair}
                                else:
                                    trade_result = await place_trade_async(pair, new_dir, alert['data']['price'], atr_val, existing_size)
                            elif existing_pos:
                                trade_result = {'skipped': True, 'reason': 'existing pos no entry time', 'pair': pair}
                            else:
                                trade_result = await place_trade_async(pair, new_dir, alert['data']['price'], atr_val, 0)

                            if 'error' not in trade_result and 'skipped' not in trade_result:
                                total = trade_result.get('total_size', trade_result['size'])
                                avg_entry = trade_result['price']
                                log.info(f"✅ Trade placed: {trade_result['action']} {trade_result['size']} (total: {total}) @ ~{trade_result['price']}")
                                pos = {
                                    'entry_price': avg_entry, 'direction': alert['data']['signal'],
                                    'size': total, 'entries': existing_pos.get('entries', 0) + 1,
                                    'entry_prices': existing_pos.get('entry_prices', []) + [avg_entry],
                                    'last_entry_time': time.time(),
                                    'highest_price': trade_result['price'], 'lowest_price': trade_result['price'],
                                    'stop_price': trade_result.get('stop_price'),
                                    'stop_order_id': trade_result.get('stop_order_id'),
                                    'entry_time': ts(), 'pair': pair,
                                }
                                positions[pair] = pos
                            elif 'error' in trade_result:
                                log.warning(f"❌ Trade failed: {trade_result['error']}")

                        alert['message'] = format_entry_alert(alert['data'], alert.get('trade_result'))
                        save_json(ALERT_FILE, alert)

                # ── Daily loss limit check (every signal tick) ──
                if not kill_switched:
                    try:
                        equity, currency = await get_account_summary_async()
                        if equity is not None:
                            if daily_start_equity is None:
                                daily_start_equity = equity
                                daily_low_equity = equity
                                log.info(f"💰 Daily starting equity: {equity:.2f} {currency or 'HKD'}")
                            daily_low_equity = min(daily_low_equity, equity)
                            daily_pnl_pct = (equity - daily_start_equity) / daily_start_equity * 100
                            if daily_pnl_pct <= DAILY_LOSS_LIMIT_PCT:
                                log.warning(f"🔴 KILL SWITCH TRIGGERED — daily P&L {daily_pnl_pct:.2f}%")
                                kill_switched = True
                    except Exception as e:
                        log.warning(f"⚠️ Could not check daily P&L: {e}")

            # ════════════════════════════════════════════
            # B. POSITION MANAGEMENT — every tick (real-time prices)
            # ════════════════════════════════════════════
            current_prices = {}
            pair_map = {'EUR/USD': 'EURUSD=X', 'AUD/JPY': 'AUDJPY=X', 'NZD/JPY': 'NZDJPY=X'}
            loop = asyncio.get_event_loop()
            for pair, ticker in pair_map.items():
                price = await loop.run_in_executor(None, fetch_current_price, ticker)
                if price is not None:
                    current_prices[pair] = price
                elif ticker in df_dict and df_dict[ticker] is not None:
                    df = df_dict[ticker]
                    c = close_series(df)
                    current_prices[pair] = float(c.iloc[-1])

            if positions:
                exits, stop_updates, positions = await manage_positions_async(
                    old_state, df_dict, all_signals, current_prices
                )
            else:
                exits, stop_updates = [], []

            # ── IBKR position sync (every signal tick) ──
            if tick_count % SIGNAL_TICK_EVERY == 1:
                ibrk_now = await sync_positions_from_ibrk_async()
                if ibrk_now:
                    for pair, ibrk_p in ibrk_now.items():
                        existing = positions.get(pair)
                        if existing and ibrk_p['size'] != existing.get('size', 0):
                            log.info(f"📡 Position size drift fixed: {pair} state={existing.get('size')} → IBKR={ibrk_p['size']}")
                            positions[pair]['size'] = ibrk_p['size']

            # ════════════════════════════════════════════
            # C. SAVE STATE
            # ════════════════════════════════════════════
            new_state = {
                'eur_usd': eur_analysis or {},
                'carry': carry_analysis,
                'positions': positions,
                'current_prices': current_prices,
                'last_tick': ts(),
            }

            # Exit events
            for exit_event in exits:
                save_json(ALERT_FILE, exit_event)
                log.info(f"🛑 {exit_event['message']}")

            if stop_updates:
                for su in stop_updates[-3:]:
                    log.info(f"↗️  Stop moved: {su['pair']} → {su['new_stop']} (ATR: {su['atr']})")

            # Kill-switch mode
            if kill_switched and old_state.get('positions'):
                log.info(f"🔴 KILL SWITCH active — managing exits only")

            # ════════════════════════════════════════════
            # D. ADAPTIVE POLLING + SLEEP
            # ════════════════════════════════════════════
            if positions:
                proximity = 30  # always warm when in positions
            else:
                proximity = calculate_proximity(all_signals, positions) if all_signals else 0
            new_interval = get_poll_interval(proximity)

            if new_interval != current_interval:
                log.info(f"📊 Proximity: {proximity} → poll {current_interval}s → {new_interval}s")
                current_interval = new_interval

            new_state['poll_interval'] = current_interval
            new_state['proximity'] = proximity
            save_json(STATE_FILE, new_state)
            old_state = new_state

            # Heartbeat every 20 ticks
            if tick_count % 20 == 0:
                parts = [f"prox:{proximity} poll:{current_interval}s"]
                if daily_start_equity is not None:
                    parts.append(f"daily:${daily_start_equity:.0f}")
                if kill_switched:
                    parts.append("🔴 KILL SWITCH")
                for sig in all_signals:
                    p = sig['pair']
                    lvl = sig.get('level', 0)
                    parts.append(f"{p}: L{lvl} {sig['signal']} RSI{sig['rsi']}")
                if positions:
                    for p, pos in positions.items():
                        parts.append(f"📌{p}: stop@{pos.get('stop_price','?')}")
                log.info(f"[Heartbeat] {' | '.join(parts)}")

            # Async sleep
            elapsed = time.time() - tick_start
            sleep_time = max(0.5, current_interval - elapsed)
            for h in log.handlers:
                h.flush()
            await asyncio.sleep(sleep_time)

        except asyncio.CancelledError:
            log.info("Daemon cancelled.")
            break
        except Exception as e:
            import traceback
            log.exception(f"💥 Tick error: {e}")
            await asyncio.sleep(10)  # back off briefly on error

    log.info("Daemon stopped.")
    disconnect_ib()


def main():
    """Entry point — runs the async daemon."""
    try:
        asyncio.run(run_daemon_async())
    except KeyboardInterrupt:
        log.info("Keyboard interrupt — shutting down.")
    finally:
        disconnect_ib()


if __name__ == '__main__':
    main()
