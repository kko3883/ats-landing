import { useEffect, useState, useCallback, useRef } from 'react'
import supabase from '../../lib/supabase'

// ── Helpers ─────────────────────────────────────────────────────────────

function groupBy(arr, keyFn) {
  const map = {}
  for (const item of arr) {
    const k = keyFn(item)
    if (!map[k]) map[k] = []
    map[k].push(item)
  }
  return map
}

const BUCKET_COLORS = {
  base_yield: { bg: 'bg-blue-900/30', border: 'border-blue-700/50', text: 'text-blue-300' },
  alpha: { bg: 'bg-purple-900/30', border: 'border-purple-700/50', text: 'text-purple-300' },
  convexity: { bg: 'bg-amber-900/30', border: 'border-amber-700/50', text: 'text-amber-300' },
}

const VIX_ZONE_LABELS = {
  high_beta: { label: 'High Beta Growth', color: 'text-rose-300' },
  moderate_beta: { label: 'Moderate Growth', color: 'text-orange-300' },
  low_beta: { label: 'Neutral', color: 'text-yellow-300' },
  defensive: { label: 'Defensive', color: 'text-emerald-300' },
}

// ── Symbol → TradingView ─────────────────────────────────────────────

// Known NYSE-listed tickers — everything else defaults to NASDAQ
const NYSE_TICKERS = new Set([
  'BRK-B', 'BRK.B', 'JPM', 'V', 'MA', 'UNH', 'JNJ', 'PG', 'XOM', 'CVX',
  'HD', 'KO', 'PEP', 'MRK', 'ABBV', 'ABT', 'WMT', 'COST', 'BA', 'CAT',
  'DIS', 'DOW', 'GS', 'HON', 'IBM', 'MMM', 'NKE', 'TRV', 'RTX', 'DE',
  'EL', 'GE', 'GM', 'LIN', 'MCD', 'MO', 'MS', 'NOC', 'PFE', 'PM',
  'SYK', 'T', 'UPS', 'USB', 'WFC', 'DELL', 'ESTC', 'SNAP', 'SQ',
  'TWLO', 'TOST', 'GTLB', 'DDOG', 'MDB', 'CFLT', 'NET',
])

// Some HK stocks don't resolve with .HK suffix — use HKEX: prefix instead
const HKEX_FALLBACK = new Set(['1928'])

function toTradingViewSymbol(symbol) {
  // TradingView accepts .HK suffix natively for most HK stocks
  // e.g. "2382.HK", "9626.HK", "1810.HK" all resolve correctly
  // Some (like 1928.HK) need HKEX: prefix
  if (symbol.endsWith('.HK')) {
    const code = symbol.replace(/\.HK$/, '')
    if (HKEX_FALLBACK.has(code)) {
      return `HKEX:${code}`
    }
    return symbol
  }
  // US tickers → prefix with exchange for reliable resolution
  const exchange = NYSE_TICKERS.has(symbol) ? 'NYSE' : 'NASDAQ'
  // Handle BRK-B → BRK.B (TradingView uses dots)
  const tvSymbol = symbol === 'BRK-B' ? 'BRK.B' : symbol
  return `${exchange}:${tvSymbol}`
}

function isHkSymbol(symbol) {
  return symbol.endsWith('.HK')
}

// ── TradingView Chart Modal (widget script, not iframe) ────────────────────

function ChartModal({ symbol, onClose }) {
  const tvSymbol = toTradingViewSymbol(symbol)
  const hk = isHkSymbol(symbol)
  const containerRef = useRef(null)
  const widgetRef = useRef(null)

  // Close on Escape key
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  // Create widget — tv.js is preloaded via _app.js
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // Clean previous widget first
    if (widgetRef.current) {
      try { widgetRef.current.remove() } catch {}
      widgetRef.current = null
    }
    container.innerHTML = ''

    const createWidget = () => {
      if (!window.TradingView) return
      widgetRef.current = new window.TradingView.widget({
        container_id: container.id,
        symbol: tvSymbol,
        interval: 'D',
        timezone: hk ? 'Asia/Hong_Kong' : 'America/New_York',
        theme: 'dark',
        style: '1',
        locale: 'en',
        toolbar_bg: '#0d1117',
        enable_publishing: false,
        hide_side_toolbar: false,
        allow_symbol_change: false,
        save_image: false,
        studies: ['RSI@tv-basicstudies', 'MASimple@tv-basicstudies'],
        studies_overrides: { 'MASimple.length': 50 },
        autosize: true,
      })
    }

    createWidget()

    return () => {
      if (widgetRef.current) {
        try { widgetRef.current.remove() } catch {}
        widgetRef.current = null
      }
    }
  }, [tvSymbol, hk])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
         onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="bg-market-900 border border-market-700 rounded-xl shadow-2xl w-full max-w-4xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-market-700">
          <div>
            <h2 className="text-lg font-bold text-white">{symbol}</h2>
            <p className="text-xs text-market-400">{tvSymbol} · Daily · Candlestick · RSI · SMA(50)</p>
          </div>
          <button
            onClick={onClose}
            className="text-market-400 hover:text-white transition-colors p-1"
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Chart container — widget mounts here */}
        <div
          id={`tv-${symbol.replace(/[^a-zA-Z0-9]/g, '-')}`}
          ref={containerRef}
          className="w-full"
          style={{ height: '480px' }}
        />
      </div>
    </div>
  )
}

// ── Indicator Panel ────────────────────────────────────────────────────
// Normalizes each indicator to a -10 (sell) to +10 (buy) scale

function normScore(value, type) {
  if (value == null) return null
  switch (type) {
    case 'rsi':    return Math.max(-10, Math.min(10, ((50 - value) / 20) * 10))
    case 'stoch':  return Math.max(-10, Math.min(10, ((50 - value) / 30) * 10))
    case 'mfi':    return Math.max(-10, Math.min(10, ((50 - value) / 30) * 10))
    case 'bb':     return Math.max(-10, Math.min(10, (0.5 - value) * 20))
    case 'macd':   return Math.max(-10, Math.min(10, value * 5))
    case 'obv':    return Math.max(-10, Math.min(10, value / 100000))
    case 'adx':    return Math.max(-10, Math.min(10, ((value - 25) / 15) * 10))
    default:       return 0
  }
}

const COMPOSITE_CFG = {
  strong_buy:  { short: 'SBuy',  color: 'text-emerald-300', bg: 'bg-emerald-900/50', bar: 'bg-emerald-500' },
  buy:         { short: 'Buy',   color: 'text-emerald-400', bg: 'bg-emerald-900/25', bar: 'bg-emerald-500' },
  hold:        { short: 'Hold',  color: 'text-yellow-300',  bg: 'bg-yellow-900/25',  bar: 'bg-yellow-500'  },
  sell:        { short: 'Sell',  color: 'text-red-400',     bg: 'bg-red-900/25',     bar: 'bg-red-500'     },
  strong_sell: { short: 'SSell', color: 'text-red-300',     bg: 'bg-red-900/50',     bar: 'bg-red-500'     },
}

function sgn(v) {
  if (v == null || isNaN(v)) return ''
  return v > 0 ? '+' : ''
}

function IndicatorBar({ rawValue, type, label }) {
  const score = normScore(rawValue, type)
  if (score == null || isNaN(score)) return null
  // Bar fill: -10 (left/red) to +10 (right/green). Center = 0 (gray).
  // Position: 0 → left edge, 1 → right edge
  const pct = (score + 10) / 20 * 100  // 0% to 100%
  const isBuy = score > 1
  const isSell = score < -1
  const barBg = isBuy ? 'bg-emerald-600' : isSell ? 'bg-red-600' : 'bg-market-500'
  return (
    <div className="flex items-center gap-1.5 text-[11px]">
      <span className="w-8 text-market-400 text-right font-mono shrink-0">{label}</span>
      <div className="flex-1 h-3 bg-market-800 rounded-sm relative overflow-hidden">
        {/* Center line */}
        <div className="absolute left-1/2 top-0 bottom-0 w-px bg-market-600 z-10" />
        {/* Fill from left or right depending on side */}
        <div
          className={`absolute top-0 bottom-0 ${barBg} rounded-sm`}
          style={score >= 0
            ? { left: '50%', width: `${pct - 50}%` }
            : { right: `${100 - pct}%`, width: `${50 - pct}%` }
          }
        />
      </div>
      <span className={`w-10 text-right font-mono ${isBuy ? 'text-emerald-300' : isSell ? 'text-red-300' : 'text-market-300'}`}>
        {sgn(score)}{score.toFixed(1)}
      </span>
    </div>
  )
}

function IndicatorPanel({ ind }) {
  if (!ind) return (
    <div className="text-[11px] text-market-500 text-center py-2 italic">No 1h indicator data</div>
  )

  const rows = [
    { key: 'macd_value', type: 'macd', label: 'MACD' },
    { key: 'adx_value', type: 'adx', label: 'ADX' },
    { key: 'rsi_value', type: 'rsi', label: 'RSI' },
    { key: 'stoch_k', type: 'stoch', label: 'Stoch' },
    { key: 'mfi_value', type: 'mfi', label: 'MFI' },
    { key: 'obv_slope', type: 'obv', label: 'OBV' },
    { key: 'bb_percent_b', type: 'bb', label: '%B' },
  ]

  const comp = ind.composite_signal || 'hold'
  const cfg = COMPOSITE_CFG[comp] || COMPOSITE_CFG.hold
  const compScore = ind.composite_score ?? 0
  const compPct = (compScore + 7) / 14 * 100  // -7 to +7 → 0% to 100%

  return (
    <div className="space-y-0.5 mt-1.5">
      <div className="text-[10px] text-market-500 uppercase tracking-wider mb-1">Indicators (1h)</div>
      {rows.map(({ key, type, label }) => (
        <IndicatorBar key={key} rawValue={ind[key]} type={type} label={label} />
      ))}
      {/* Composite bar — different scale: -7 to +7 */}
      <div className="flex items-center gap-1.5 text-[11px] mt-1.5 pt-1.5 border-t border-market-800">
        <span className="w-8 text-market-300 text-right font-mono shrink-0 text-[10px]">Cmp</span>
        <div className="flex-1 h-3.5 bg-market-800 rounded-sm relative overflow-hidden">
          <div className="absolute left-1/2 top-0 bottom-0 w-px bg-market-600 z-10" />
          <div
            className={`absolute top-0 bottom-0 ${cfg.bar} rounded-sm`}
            style={compScore >= 0
              ? { left: '50%', width: `${compPct - 50}%` }
              : { right: `${100 - compPct}%`, width: `${50 - compPct}%` }
            }
          />
        </div>
        <span className={`w-10 text-right font-mono font-bold ${cfg.color}`}>
          {sgn(compScore)}{compScore}
        </span>
      </div>
      {/* Raw values */}
      <div className="text-[9px] text-market-600 text-center mt-0.5">
        {rows.map(({ key, type, label }) => {
          const v = ind[key]
          if (v == null) return null
          return <span key={key} className="mr-2">{label}: {typeof v === 'number' ? (Math.abs(v) < 100 ? v.toFixed(2) : v.toFixed(0)) : v}</span>
        })}
      </div>
      {/* Entry/Stop/Target */}
      {ind.atr_value != null && (
        <div className="text-[10px] text-market-400 text-center mt-1 pt-1 border-t border-market-800/50">
          ATR: <span className="text-market-300">${ind.atr_value.toFixed(2)}</span>
          {' · '}Stop: <span className="text-red-400">${ind.stop_loss?.toFixed(2) ?? '—'}</span>
          {' · '}Target: <span className="text-emerald-400">${ind.take_profit?.toFixed(2) ?? '—'}</span>
        </div>
      )}
    </div>
  )
}

function CompositeBadge({ signal }) {
  const cfg = COMPOSITE_CFG[signal] || COMPOSITE_CFG.hold
  return (
    <span className={`text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${cfg.bg} ${cfg.color}`}>
      {cfg.short}
    </span>
  )
}

// Helper: normalize ticker key for indicator lookup
function indicatorKey(ticker) {
  if (ticker.endsWith('.HK') || ticker.endsWith('.US')) return ticker
  return `${ticker}.US`
}

// ── Alignment detection ───────────────────────────────────────────────
// Checks if screener direction agrees with indicator composite

function getAlignment(screenerDirection, indicatorSignal) {
  // screenerDirection: 'LONG' or 'SHORT' (or 'long'/'short')
  // indicatorSignal: 'strong_buy','buy','hold','sell','strong_sell'
  const isLong = screenerDirection?.toLowerCase() === 'long'
  const isBullish = ['strong_buy', 'buy'].includes(indicatorSignal)
  const isBearish = ['strong_sell', 'sell'].includes(indicatorSignal)

  if (isLong && isBullish) return 'aligned'
  if (!isLong && isBearish) return 'aligned'
  if (isLong && isBearish) return 'conflict'
  if (!isLong && isBullish) return 'conflict'
  return 'neutral'
}

const ALIGN_CFG = {
  aligned:  { label: '✓ Align', bg: 'bg-emerald-800/40', text: 'text-emerald-300', border: 'border-emerald-500/40' },
  neutral:  { label: '~ Wait',  bg: 'bg-yellow-800/30',  text: 'text-yellow-300',  border: 'border-yellow-500/30' },
  conflict: { label: '✗ Conflict', bg: 'bg-red-800/40',   text: 'text-red-300',    border: 'border-red-500/40' },
}

function AlignmentBadge({ direction, indicatorSignal }) {
  const state = getAlignment(direction, indicatorSignal)
  const cfg = ALIGN_CFG[state]
  if (!cfg) return null
  return (
    <span className={`text-[9px] font-mono px-1 py-0.5 rounded ${cfg.bg} ${cfg.text}`}>
      {cfg.label}
    </span>
  )
}

// ── Regime badge ──────────────────────────────────────────────────────

const REGIME_CFG = {
  risk_on: { label: 'Risk-On ▲',  color: 'text-emerald-300', bg: 'bg-emerald-900/40' },
  choppy:  { label: 'Choppy ~',   color: 'text-yellow-300',  bg: 'bg-yellow-900/30' },
  risk_off:{ label: 'Risk-Off ▼', color: 'text-red-300',     bg: 'bg-red-900/40' },
}

function regimeBadge(r) {
  const cfg = REGIME_CFG[r.regime_name] || REGIME_CFG.choppy
  return (
    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${cfg.bg} ${cfg.color}`}>
      {cfg.label} VIX {r.vix_level?.toFixed(1)}
    </span>
  )
}

function SignalCard({ signal, onChart, stockNames, ind, held }) {
  const bucket = signal.bucket || 'alpha'
  const style = BUCKET_COLORS[bucket] || BUCKET_COLORS.alpha
  const meta = signal.signal_json || {}
  const ticker = signal.ticker
  const name = stockNames?.[ticker] || stockNames?.[`${ticker}.US`] || ''
  const vixLabel = VIX_ZONE_LABELS[signal.vix_zone]?.label || signal.vix_zone || '—'
  const tickerHeld = held?.has(ticker) || held?.has(`${ticker}.US`)

  return (
    <div
      className={`${style.bg} ${style.border} border rounded-lg p-3 cursor-pointer transition-colors group hover:brightness-110`}
      onClick={() => onChart?.(ticker)}
    >
      {/* Header: ticker + badges */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-bold text-white text-sm truncate">{ticker}</span>
          {tickerHeld && <span className="text-[9px] font-mono px-1 py-0.5 rounded bg-blue-800/40 text-blue-300 shrink-0">Held</span>}
          {name && <span className="text-[10px] text-market-500 truncate hidden sm:block">{name}</span>}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <AlignmentBadge direction={direction} indicatorSignal={ind?.composite_signal} />
          <CompositeBadge signal={ind?.composite_signal} />
          <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded-full ${signal.direction === 'LONG' ? 'bg-emerald-800/50 text-emerald-200' : 'bg-red-800/50 text-red-200'}`}>
            {signal.direction}
          </span>
        </div>
      </div>

      {/* Two-column layout: screener | technical indicators */}
      <div className="flex gap-2">
        {/* Left: screener data */}
        <div className="w-24 shrink-0 space-y-1 text-[10px]">
          <div className="flex justify-between text-market-400">
            <span>VIX Zone</span>
            <span className="text-market-300">{vixLabel}</span>
          </div>
          <div className="flex justify-between text-market-400">
            <span>Bucket</span>
            <span className={style.text}>{bucket}</span>
          </div>
          {meta.group && (
            <div className="flex justify-between text-market-400">
              <span>Group</span>
              <span className="text-market-300">{meta.group.replace(/_/g, ' ')}</span>
            </div>
          )}
          {meta.price && (
            <div className="flex justify-between text-market-400">
              <span>Price</span>
              <span className="text-white">${typeof meta.price === 'number' ? meta.price.toFixed(2) : meta.price}</span>
            </div>
          )}
        </div>
        {/* Right: technical indicators */}
        <div className="flex-1 min-w-0">
          <IndicatorPanel ind={ind} />
        </div>
      </div>
    </div>
  )
}

function HkCard({ symbol, candidate_type, rs_zscore, beta_vix, beta_dxy, beta_group, onChart, stockNames, onRsInfo, ind, held }) {
  const isLong = candidate_type === 'long'
  const name = stockNames?.[symbol] || ''
  const tickerHeld = held?.has(symbol) || held?.has(symbol.replace('.HK', ''))
  return (
    <div
      className={`${isLong ? 'bg-emerald-900/20 border-emerald-700/40 hover:bg-emerald-900/40' : 'bg-red-900/20 border-red-700/40 hover:bg-red-900/40'} border rounded-lg p-3 cursor-pointer transition-colors group`}
      onClick={() => onChart?.(symbol)}
    >
      {/* Header: ticker + badges */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-bold text-white text-sm truncate">{symbol}</span>
          {tickerHeld && <span className="text-[9px] font-mono px-1 py-0.5 rounded bg-blue-800/40 text-blue-300 shrink-0">Held</span>}
          {name && <span className="text-[10px] text-market-500 truncate hidden sm:block">{name}</span>}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <AlignmentBadge direction={candidate_type} indicatorSignal={ind?.composite_signal} />
          <CompositeBadge signal={ind?.composite_signal} />
          <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${isLong ? 'bg-emerald-800/50 text-emerald-200' : 'bg-red-800/50 text-red-200'}`}>
            {isLong ? 'LONG' : 'SHORT'}
          </span>
        </div>
      </div>

      {/* Two-column: screener | technical indicators */}
      <div className="flex gap-2">
        {/* Left: screener data */}
        <div className="w-24 shrink-0 space-y-1 text-[10px]">
          <div className="flex items-center justify-between text-market-400">
            <span className="flex items-center gap-0.5">
              RS Z
              <button onClick={(e) => { e.stopPropagation(); onRsInfo?.(); }} className="text-market-500 hover:text-market-300 leading-none">
                <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </button>
            </span>
            <span className={isLong ? 'text-emerald-300' : 'text-red-300'}>{rs_zscore > 0 ? '+' : ''}{typeof rs_zscore === 'number' ? rs_zscore.toFixed(2) : rs_zscore}</span>
          </div>
          {beta_vix != null && (
            <div className="flex justify-between text-market-400">
              <span>VIX β</span>
              <span className={beta_vix < 0 ? 'text-rose-300' : 'text-emerald-300'}>{beta_vix > 0 ? '+' : ''}{typeof beta_vix === 'number' ? beta_vix.toFixed(2) : beta_vix}</span>
            </div>
          )}
          {beta_dxy != null && (
            <div className="flex justify-between text-market-400">
              <span>DXY β</span>
              <span className={beta_dxy < 0 ? 'text-rose-300' : 'text-emerald-300'}>{beta_dxy > 0 ? '+' : ''}{typeof beta_dxy === 'number' ? beta_dxy.toFixed(2) : beta_dxy}</span>
            </div>
          )}
          {beta_group && (
            <div className="flex justify-between text-market-400">
              <span>Group</span>
              <span className="text-market-300">{beta_group.replace('group_', '').replace('_', ' ')}</span>
            </div>
          )}
        </div>
        {/* Right: technical indicators */}
        <div className="flex-1 min-w-0">
          <IndicatorPanel ind={ind} />
        </div>
      </div>
    </div>
  )
}

function BucketSection({ title, signals, style, onChart, stockNames, indicators, held }) {
  if (!signals || signals.length === 0) return null
  return (
    <div className="mb-6">
      <h3 className={`text-sm font-semibold uppercase tracking-wider mb-3 ${style.text}`}>{title} ({signals.length})</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {signals.map(s => <SignalCard key={s.id} signal={s} onChart={onChart} stockNames={stockNames} ind={indicators?.[indicatorKey(s.ticker)]} held={held} />)}
      </div>
    </div>
  )
}

// ── Main Component ──────────────────────────────────────────────────────

export default function Dashboard() {
  const [connected, setConnected] = useState(!!supabase)
  const [signals, setSignals] = useState([])
  const [hkWatchlist, setHkWatchlist] = useState([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('all')
  const [chartSymbol, setChartSymbol] = useState(null) // symbol to show chart for, or null
  const [stockNames, setStockNames] = useState({}) // {symbol: name}
  const [rsExplainer, setRsExplainer] = useState(null) // show RS tooltip
  const [indicatorSignals, setIndicatorSignals] = useState({}) // {ticker: indicator_row}
  const [regime, setRegime] = useState(null) // current market regime
  const [positions, setPositions] = useState([]) // held tickers

  // Load stock names map
  useEffect(() => {
    fetch('/data/stock_names.json')
      .then(r => r.json())
      .then(data => {
        const flat = {}
        for (const market of ['us', 'hk']) {
          if (data[market]) {
            Object.assign(flat, data[market])
          }
        }
        setStockNames(flat)
      })
      .catch(() => {}) // silently fail, names are optional
  }, [])

  useEffect(() => {
    if (!supabase) {
      setConnected(false)
      setLoading(false)
      return
    }

    setConnected(true)

    supabase
      .from('signals')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(50)
      .then(({ data, error }) => {
        if (!error && data) {
          // Dedup: keep only the latest signal per ticker
          const seen = new Set()
          const unique = []
          for (const row of data) {
            const key = row.ticker
            if (!seen.has(key)) {
              seen.add(key)
              unique.push(row)
            }
          }
          setSignals(unique)
        }
      })

    supabase
      .from('watchlist_hk')
      .select('*')
      .order('id', { ascending: false })
      .limit(50)
      .then(({ data, error }) => {
        if (!error && data) {
          const latestBatch = data[0]?.generated_at
          setHkWatchlist(latestBatch ? data.filter(r => r.generated_at === latestBatch) : data)
        }
        setLoading(false)
      })

    // Fetch latest indicator signals
    supabase
      .from('indicator_signals')
      .select('*')
      .order('calculated_at', { ascending: false })
      .limit(100)
      .then(({ data, error }) => {
        if (!error && data) {
          const map = {}
          for (const row of data) {
            if (!map[row.ticker]) {
              map[row.ticker] = row
            }
          }
          setIndicatorSignals(map)
        }
      })

    // Fetch latest regime
    supabase
      .from('regime')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(1)
      .then(({ data }) => {
        if (data && data.length > 0) setRegime(data[0])
      })

    // Fetch positions (held stocks)
    supabase
      .from('portfolio')
      .select('ticker')
      .then(({ data }) => {
        if (data) setPositions(data.map(r => r.ticker))
      })
    // Real-time: listen for new signals (dedup by ticker)
    const channel = supabase
      .channel('signals')
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'signals' },
        (payload) => setSignals(prev => {
          const existing = prev.find(s => s.ticker === payload.new.ticker)
          if (existing) {
            // Replace outdated signal with newer one
            return prev.map(s => s.ticker === payload.new.ticker ? payload.new : s)
          }
          return [payload.new, ...prev]
        })
      )
      .subscribe()

    return () => { supabase.removeChannel(channel) }
  }, [])

  // ── Not connected state ──
  if (!connected) {
    return (
      <div className="min-h-screen bg-market-950 text-market-100 flex items-center justify-center">
        <div className="text-center max-w-md px-4">
          <div className="text-4xl mb-4">🔒</div>
          <h1 className="text-2xl font-bold text-white mb-3">Dashboard Coming Soon</h1>
          <p className="text-market-400 mb-2">
            The live dashboard will appear here once Supabase is connected.
          </p>
          <p className="text-sm text-market-500">
            Set <code className="text-green-400 text-xs bg-market-800 px-1 py-0.5 rounded">NEXT_PUBLIC_SUPABASE_URL</code> and{' '}
            <code className="text-green-400 text-xs bg-market-800 px-1 py-0.5 rounded">NEXT_PUBLIC_SUPABASE_ANON_KEY</code>{' '}
            to enable.
          </p>
          <a
            href="/"
            className="inline-block mt-8 px-6 py-2.5 rounded-lg border border-market-700 text-market-300 text-sm hover:text-white transition-colors"
          >
            ← Back to Home
          </a>
        </div>
      </div>
    )
  }

  // ── Group signals ──
  const byVixZone = groupBy(signals, s => s.vix_zone || 'unknown')
  const zoneOrder = ['high_beta', 'moderate_beta', 'low_beta', 'defensive']

  const hkLong = hkWatchlist.filter(r => r.candidate_type === 'long').sort((a, b) => b.rs_zscore - a.rs_zscore)
  const hkShort = hkWatchlist.filter(r => r.candidate_type === 'short').sort((a, b) => a.rs_zscore - b.rs_zscore)

  // Held positions set
  const heldSet = new Set(positions)

  // ── Chart modal ──
  const openChart = useCallback((symbol) => setChartSymbol(symbol), [])
  const closeChart = useCallback(() => setChartSymbol(null), [])

  // ── Main Dashboard ──
  return (
    <div className="min-h-screen bg-market-950 text-market-100">
      <div className="max-w-6xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-white">Dashboard</h1>
            <p className="text-sm text-market-400 mt-1">
              {signals.length} signals · {hkWatchlist.length} HK watchlist · {Object.keys(indicatorSignals).length} indicators
              {regime && <span className="ml-2">{regimeBadge(regime)}</span>}
            </p>
          </div>
          <a href="/" className="text-sm text-market-500 hover:text-market-300 transition-colors">
            ← Home
          </a>
        </div>

        {/* Tab bar */}
        <div className="flex gap-1 mb-6 border-b border-market-800">
          {[
            { key: 'all', label: 'All' },
            { key: 'us', label: 'US Signals' },
            { key: 'hk', label: 'HK Watchlist' },
          ].map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
                tab === t.key
                  ? 'bg-market-800 text-white border-b-2 border-blue-500'
                  : 'text-market-500 hover:text-market-300'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {loading && (
          <div className="text-center py-12 text-market-500">
            <div className="animate-spin inline-block w-6 h-6 border-2 border-market-600 border-t-blue-500 rounded-full mb-3" />
            <p>Loading...</p>
          </div>
        )}

        {!loading && tab !== 'hk' && (
          <div>
            <div className="mb-2">
              <h2 className="text-lg font-semibold text-white">US Signals</h2>
              <p className="text-xs text-market-500 mb-4">From signal engine · Click any ticker for chart · Auto-refreshes in real-time</p>
            </div>

            {signals.length === 0 ? (
              <div className="text-center py-12 border border-dashed border-market-700 rounded-lg">
                <p className="text-market-500">No signals yet. The engine will publish them here when it runs.</p>
              </div>
            ) : (
              zoneOrder.map(zone => (
                <BucketSection
                  key={zone}
                  title={VIX_ZONE_LABELS[zone]?.label || zone}
                  signals={byVixZone[zone] || []}
                  style={BUCKET_COLORS.alpha}
                  onChart={openChart}
                  stockNames={stockNames}
                  indicators={indicatorSignals}
                  held={heldSet}
                />
              ))
            )}

            {byVixZone['unknown'] && byVixZone['unknown'].length > 0 && (
              <BucketSection
                title="Other"
                signals={byVixZone['unknown']}
                style={BUCKET_COLORS.convexity}
                onChart={openChart}
                stockNames={stockNames}
                indicators={indicatorSignals}
                held={heldSet}
              />
            )}
          </div>
        )}

        {!loading && tab !== 'us' && (
          <div>
            <div className="mb-2">
              <h2 className="text-lg font-semibold text-white">HK Watchlist</h2>
              <p className="text-xs text-market-500 mb-4">
                From Longbridge market data · Click any symbol for chart · Updated daily after market close
              </p>
            </div>

            {hkWatchlist.length === 0 ? (
              <div className="text-center py-12 border border-dashed border-market-700 rounded-lg">
                <p className="text-market-500">HK watchlist will appear here after the daily screening runs.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div>
                  <h3 className="text-sm font-semibold uppercase tracking-wider text-emerald-400 mb-3">
                    Top Long Candidates ({hkLong.length})
                  </h3>
                  <div className="space-y-2">
                    {hkLong.map((r, i) => (
                      <HkCard key={`${r.id}-${i}`} {...r} onChart={openChart} stockNames={stockNames} onRsInfo={() => setRsExplainer('rs')} ind={indicatorSignals[r.symbol]} held={heldSet} />
                    ))}
                  </div>
                </div>

                <div>
                  <h3 className="text-sm font-semibold uppercase tracking-wider text-red-400 mb-3">
                    Top Short Candidates ({hkShort.length})
                  </h3>
                  <div className="space-y-2">
                    {hkShort.map((r, i) => (
                      <HkCard key={`${r.id}-${i}`} {...r} onChart={openChart} stockNames={stockNames} onRsInfo={() => setRsExplainer('rs')} ind={indicatorSignals[r.symbol]} held={heldSet} />
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* RS Z-Score Explainer */}
      {rsExplainer && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
             onClick={() => setRsExplainer(null)}>
          <div className="bg-market-900 border border-market-700 rounded-xl shadow-2xl max-w-lg w-full p-6" onClick={e => e.stopPropagation()}>
            <div className="flex items-start justify-between mb-4">
              <div>
                <h3 className="text-lg font-bold text-white">What is RS Z-Score?</h3>
                <p className="text-xs text-market-400 mt-1">Relative Strength Z-Score</p>
              </div>
              <button onClick={() => setRsExplainer(null)} className="text-market-400 hover:text-white p-1">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="space-y-3 text-sm text-market-300">
              <p>
                <strong className="text-white">RS Z-Score</strong> measures how a stock performed
                relative to its peer group over the past month.
              </p>
              <div className="bg-market-800 rounded-lg p-3 text-xs space-y-2">
                <div className="flex justify-between">
                  <span>Calculation:</span>
                  <span className="text-market-400 text-right w-3/5">(stock_return − group_mean) ÷ group_std</span>
                </div>
                <div className="flex justify-between">
                  <span>Groups are:</span>
                  <span className="text-market-400 text-right w-3/5">Stocks with similar VIX beta</span>
                </div>
              </div>
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className="text-emerald-400 font-bold">+Z</span>
                  <span>Outperforming peers → <strong className="text-white">Long candidates</strong> (top 10%)</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-red-400 font-bold">−Z</span>
                  <span>Underperforming peers → <strong className="text-white">Short candidates</strong> (bottom 10%)</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-market-500 font-bold">|Z| &gt; 2</span>
                  <span>Statistically significant — strong momentum signal</span>
                </div>
              </div>
              <p className="text-xs text-market-500 italic mt-2">
                RS Z-Score is a cross-sectional metric comparing stocks within the same
                VIX beta group. It captures relative momentum adjusted for macro risk exposure.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Chart Modal — key forces clean unmount/remount on symbol change */}
      {chartSymbol && (
        <ChartModal key={chartSymbol} symbol={chartSymbol} onClose={closeChart} />
      )}
    </div>
  )
}
