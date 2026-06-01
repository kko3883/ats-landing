import { useEffect, useState, useCallback, useRef } from 'react'
import supabase from '../../lib/supabase'

// ── Helpers ─────────────────────────────────────────────────────────────

const BUCKET_LABELS = { base_yield: 'Base Yield', alpha: 'Alpha', convexity: 'Convexity', existing: 'Existing' }
const VIX_ZONE_LABELS = {
  high_beta: { label: 'High Beta Growth', color: 'text-rose-300' },
  moderate_beta: { label: 'Moderate Growth', color: 'text-orange-300' },
  low_beta: { label: 'Neutral', color: 'text-yellow-300' },
  defensive: { label: 'Defensive', color: 'text-emerald-300' },
}

// ── Symbol → TradingView ─────────────────────────────────────────────

const NYSE_TICKERS = new Set([
  'BRK-B', 'BRK.B', 'JPM', 'V', 'MA', 'UNH', 'JNJ', 'PG', 'XOM', 'CVX',
  'HD', 'KO', 'PEP', 'MRK', 'ABBV', 'ABT', 'WMT', 'COST', 'BA', 'CAT',
  'DIS', 'DOW', 'GS', 'HON', 'IBM', 'MMM', 'NKE', 'TRV', 'RTX', 'DE',
  'EL', 'GE', 'GM', 'LIN', 'MCD', 'MO', 'MS', 'NOC', 'PFE', 'PM',
  'SYK', 'T', 'UPS', 'USB', 'WFC', 'DELL', 'ESTC', 'SNAP', 'SQ',
  'TWLO', 'TOST', 'GTLB', 'DDOG', 'MDB', 'CFLT', 'NET',
])

const HKEX_FALLBACK = new Set(['1928'])

function toTradingViewSymbol(symbol) {
  if (symbol.endsWith('.HK')) {
    const code = symbol.replace(/\.HK$/, '')
    if (HKEX_FALLBACK.has(code)) return `HKEX:${code}`
    return symbol
  }
  const exchange = NYSE_TICKERS.has(symbol) ? 'NYSE' : 'NASDAQ'
  const tvSymbol = symbol === 'BRK-B' ? 'BRK.B' : symbol
  return `${exchange}:${tvSymbol}`
}

function isHkSymbol(symbol) { return symbol.endsWith('.HK') }

// Normalize ticker for indicator lookup
function indicatorKey(ticker) {
  if (ticker.endsWith('.HK') || ticker.endsWith('.US')) return ticker
  return `${ticker}.US`
}

// ── TradingView Chart Modal ─────────────────────────────────────────────

function ChartModal({ symbol, onClose }) {
  const tvSymbol = toTradingViewSymbol(symbol)
  const hk = isHkSymbol(symbol)
  const containerRef = useRef(null)
  const widgetRef = useRef(null)

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
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
        <div className="flex items-center justify-between px-5 py-3 border-b border-market-700">
          <div>
            <h2 className="text-lg font-bold text-white">{symbol}</h2>
            <p className="text-xs text-market-400">{tvSymbol} · Daily · Candlestick · RSI · SMA(50)</p>
          </div>
          <button onClick={onClose} className="text-market-400 hover:text-white transition-colors p-1">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div id={`tv-${symbol.replace(/[^a-zA-Z0-9]/g, '-')}`} ref={containerRef} className="w-full" style={{ height: '480px' }} />
      </div>
    </div>
  )
}

// ── Indicator Panel ────────────────────────────────────────────────────

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
  const pct = (score + 10) / 20 * 100
  const isBuy = score > 1
  const isSell = score < -1
  const barBg = isBuy ? 'bg-emerald-600' : isSell ? 'bg-red-600' : 'bg-market-500'
  return (
    <div className="flex items-center gap-1.5 text-[11px]">
      <span className="w-8 text-market-400 text-right font-mono shrink-0">{label}</span>
      <div className="flex-1 h-3 bg-market-800 rounded-sm relative overflow-hidden">
        <div className="absolute left-1/2 top-0 bottom-0 w-px bg-market-600 z-10" />
        <div className={`absolute top-0 bottom-0 ${barBg} rounded-sm`}
          style={score >= 0
            ? { left: '50%', width: `${pct - 50}%` }
            : { right: `${100 - pct}%`, width: `${50 - pct}%` }
          } />
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
  const compPct = (compScore + 7) / 14 * 100

  return (
    <div className="space-y-0.5 mt-1.5">
      <div className="text-[10px] text-market-500 uppercase tracking-wider mb-1">Indicators (1h)</div>
      {rows.map(({ key, type, label }) => (
        <IndicatorBar key={key} rawValue={ind[key]} type={type} label={label} />
      ))}
      <div className="flex items-center gap-1.5 text-[11px] mt-1.5 pt-1.5 border-t border-market-800">
        <span className="w-8 text-market-300 text-right font-mono shrink-0 text-[10px]">Cmp</span>
        <div className="flex-1 h-3.5 bg-market-800 rounded-sm relative overflow-hidden">
          <div className="absolute left-1/2 top-0 bottom-0 w-px bg-market-600 z-10" />
          <div className={`absolute top-0 bottom-0 ${cfg.bar} rounded-sm`}
            style={compScore >= 0
              ? { left: '50%', width: `${compPct - 50}%` }
              : { right: `${100 - compPct}%`, width: `${50 - compPct}%` }
            } />
        </div>
        <span className={`w-10 text-right font-mono font-bold ${cfg.color}`}>
          {sgn(compScore)}{compScore}
        </span>
      </div>
      <div className="text-[9px] text-market-600 text-center mt-0.5">
        {rows.map(({ key, type, label }) => {
          const v = ind[key]
          if (v == null) return null
          return <span key={key} className="mr-2">{label}: {typeof v === 'number' ? (Math.abs(v) < 100 ? v.toFixed(2) : v.toFixed(0)) : v}</span>
        })}
      </div>
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

// ── Alignment ──────────────────────────────────────────────────────────

function getAlignment(screenerDirection, indicatorSignal) {
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

// ── RS Explainer modal ────────────────────────────────────────────────

function RsExplainerModal({ onClose }) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
         onClick={onClose}>
      <div className="bg-market-900 border border-market-700 rounded-xl shadow-2xl max-w-lg w-full p-6" onClick={e => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="text-lg font-bold text-white">What is RS Z-Score?</h3>
            <p className="text-xs text-market-400 mt-1">Relative Strength Z-Score</p>
          </div>
          <button onClick={onClose} className="text-market-400 hover:text-white p-1">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="space-y-3 text-sm text-market-300">
          <p><strong className="text-white">RS Z-Score</strong> measures how a stock performed relative to its peer group over the past month.</p>
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
  )
}

// ── Unified Card ──────────────────────────────────────────────────────
// Expects: item = { ticker, direction, isHeld, price, vix_zone, bucket, group, qty, rs_zscore, beta_vix, beta_dxy, beta_group }

function UnifiedCard({ item, onChart, stockNames, onRsInfo, ind }) {
  const { ticker, direction, isHeld, price, vix_zone, bucket, group, qty, rs_zscore, beta_vix, beta_dxy, beta_group } = item

  const name = stockNames?.[ticker] || stockNames?.[ticker.replace(/\.(US|HK)$/, '')] || ''

  const isLong = direction === 'LONG'
  const isShort = direction === 'SHORT'

  // Colour by direction: LONG=emerald, HOLD=blue, SHORT=red
  const colors = isLong
    ? { bg: 'bg-emerald-900/20', border: 'border-emerald-700/40', hover: 'hover:bg-emerald-900/40' }
    : isShort
    ? { bg: 'bg-red-900/20', border: 'border-red-700/40', hover: 'hover:bg-red-900/40' }
    : { bg: 'bg-blue-900/20', border: 'border-blue-700/40', hover: 'hover:bg-blue-900/30' }

  const vixLabel = VIX_ZONE_LABELS[vix_zone]?.label || vix_zone || '—'
  const bktLabel = BUCKET_LABELS[bucket] || bucket || '—'

  return (
    <div
      className={`${colors.bg} ${colors.border} ${colors.hover} border rounded-lg p-3 cursor-pointer transition-colors group`}
      onClick={() => onChart?.(ticker)}
    >
      {/* Header: ticker + badges */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-bold text-white text-sm truncate">{ticker}</span>
          {isHeld && <span className="text-[9px] font-mono px-1 py-0.5 rounded bg-blue-800/40 text-blue-300 shrink-0">Held</span>}
          {name && <span className="text-[10px] text-market-500 truncate hidden sm:block">{name}</span>}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {ind?.composite_signal && <AlignmentBadge direction={direction} indicatorSignal={ind.composite_signal} />}
          {ind?.composite_signal && <CompositeBadge signal={ind.composite_signal} />}
          <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
            isLong ? 'bg-emerald-800/50 text-emerald-200' :
            isShort ? 'bg-red-800/50 text-red-200' :
            'bg-blue-800/50 text-blue-200'
          }`}>
            {direction}
          </span>
        </div>
      </div>

      {/* Two-column: screener data | technical indicators */}
      <div className="flex gap-2">
        <div className="w-24 shrink-0 space-y-1 text-[10px]">
          {vix_zone && (
            <div className="flex justify-between text-market-400">
              <span>VIX Zone</span>
              <span className="text-market-300">{vixLabel}</span>
            </div>
          )}
          {bucket && (
            <div className="flex justify-between text-market-400">
              <span>Bucket</span>
              <span className={colors.border.replace('border-', 'text-').replace('/40', '') || 'text-market-300'}>{bktLabel}</span>
            </div>
          )}
          {group && (
            <div className="flex justify-between text-market-400">
              <span>Group</span>
              <span className="text-market-300">{group.replace(/_/g, ' ')}</span>
            </div>
          )}
          {price != null && (
            <div className="flex justify-between text-market-400">
              <span>Price</span>
              <span className="text-white">${typeof price === 'number' ? price.toFixed(2) : price}</span>
            </div>
          )}
          {qty != null && (
            <div className="flex justify-between text-market-400">
              <span>Qty</span>
              <span className="text-white">{qty}</span>
            </div>
          )}
          {rs_zscore != null && (
            <div className="flex items-center justify-between text-market-400">
              <span className="flex items-center gap-0.5">
                RS Z
                <button onClick={(e) => { e.stopPropagation(); onRsInfo?.(); }} className="text-market-500 hover:text-market-300 leading-none">
                  <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </button>
              </span>
              <span className={isLong ? 'text-emerald-300' : isShort ? 'text-red-300' : 'text-market-300'}>
                {rs_zscore > 0 ? '+' : ''}{typeof rs_zscore === 'number' ? rs_zscore.toFixed(2) : rs_zscore}
              </span>
            </div>
          )}
          {beta_vix != null && (
            <div className="flex justify-between text-market-400">
              <span>VIX β</span>
              <span className={beta_vix < 0 ? 'text-rose-300' : 'text-emerald-300'}>
                {beta_vix > 0 ? '+' : ''}{typeof beta_vix === 'number' ? beta_vix.toFixed(2) : beta_vix}
              </span>
            </div>
          )}
          {beta_dxy != null && (
            <div className="flex justify-between text-market-400">
              <span>DXY β</span>
              <span className={beta_dxy < 0 ? 'text-rose-300' : 'text-emerald-300'}>
                {beta_dxy > 0 ? '+' : ''}{typeof beta_dxy === 'number' ? beta_dxy.toFixed(2) : beta_dxy}
              </span>
            </div>
          )}
          {beta_group && (
            <div className="flex justify-between text-market-400">
              <span>Group</span>
              <span className="text-market-300">{beta_group.replace('group_', '').replace(/_/g, ' ')}</span>
            </div>
          )}
        </div>
        <div className="flex-1 min-w-0">
          <IndicatorPanel ind={ind} />
        </div>
      </div>
    </div>
  )
}

// ── Section ────────────────────────────────────────────────────────────

function Section({ title, items, onChart, stockNames, onRsInfo, indicators }) {
  if (!items || items.length === 0) return null
  const headerColor =
    title === 'Long' ? 'text-emerald-400' :
    title === 'Short' ? 'text-red-400' :
    'text-blue-400'
  return (
    <div className="mb-6">
      <h2 className={`text-sm font-semibold uppercase tracking-wider mb-3 ${headerColor}`}>
        {title} ({items.length})
      </h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {items.map(item => (
          <UnifiedCard
            key={item.ticker}
            item={item}
            onChart={onChart}
            stockNames={stockNames}
            onRsInfo={onRsInfo}
            ind={indicators?.[indicatorKey(item.ticker)]}
          />
        ))}
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
  const [chartSymbol, setChartSymbol] = useState(null)
  const [stockNames, setStockNames] = useState({})
  const [rsExplainer, setRsExplainer] = useState(false)
  const [indicatorSignals, setIndicatorSignals] = useState({})
  const [regime, setRegime] = useState(null)
  const [positions, setPositions] = useState([])

  // Load stock names map
  useEffect(() => {
    fetch('/data/stock_names.json')
      .then(r => r.json())
      .then(data => {
        const flat = {}
        for (const market of ['us', 'hk']) {
          if (data[market]) Object.assign(flat, data[market])
        }
        setStockNames(flat)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!supabase) {
      setConnected(false)
      setLoading(false)
      return
    }

    setConnected(true)

    supabase.from('signals').select('*').order('created_at', { ascending: false }).limit(50)
      .then(({ data, error }) => {
        if (!error && data) {
          const seen = new Set()
          const unique = []
          for (const row of data) {
            if (!seen.has(row.ticker)) {
              seen.add(row.ticker)
              unique.push(row)
            }
          }
          setSignals(unique)
        }
      })

    supabase.from('watchlist_hk').select('*').order('id', { ascending: false }).limit(50)
      .then(({ data, error }) => {
        if (!error && data) {
          const latestBatch = data[0]?.generated_at
          setHkWatchlist(latestBatch ? data.filter(r => r.generated_at === latestBatch) : data)
        }
        setLoading(false)
      })

    supabase.from('indicator_signals').select('*').order('calculated_at', { ascending: false }).limit(100)
      .then(({ data, error }) => {
        if (!error && data) {
          const map = {}
          for (const row of data) {
            if (!map[row.ticker]) map[row.ticker] = row
          }
          setIndicatorSignals(map)
        }
      })

    supabase.from('regime').select('*').order('created_at', { ascending: false }).limit(1)
      .then(({ data }) => {
        if (data && data.length > 0) setRegime(data[0])
      })

    supabase.from('portfolio').select('ticker,position_qty,market_value,bucket')
      .then(({ data }) => {
        if (data) setPositions(data)
      })

    const channel = supabase.channel('signals')
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'signals' },
        (payload) => setSignals(prev => {
          const existing = prev.find(s => s.ticker === payload.new.ticker)
          if (existing) return prev.map(s => s.ticker === payload.new.ticker ? payload.new : s)
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
          <p className="text-market-400 mb-2">The live dashboard will appear here once Supabase is connected.</p>
          <p className="text-sm text-market-500">
            Set <code className="text-green-400 text-xs bg-market-800 px-1 py-0.5 rounded">NEXT_PUBLIC_SUPABASE_URL</code> and{' '}
            <code className="text-green-400 text-xs bg-market-800 px-1 py-0.5 rounded">NEXT_PUBLIC_SUPABASE_ANON_KEY</code>{' '}
            to enable.
          </p>
          <a href="/" className="inline-block mt-8 px-6 py-2.5 rounded-lg border border-market-700 text-market-300 text-sm hover:text-white transition-colors">← Back to Home</a>
        </div>
      </div>
    )
  }

  // ── Build unified item list ───────────────────────────────────────────
  // 1) From signals table (LONG/SHORT)
  // 2) From HK watchlist (long/short candidates)
  // 3) From portfolio (HOLD) — only tickers NOT already in LONG or SHORT

  const dirSignalMap = {}  // ticker -> { item }

  // Signals table
  for (const s of signals) {
    const meta = s.signal_json || {}
    dirSignalMap[s.ticker] = {
      ticker: s.ticker,
      direction: s.direction,
      isHeld: false,  // will set below
      price: meta.price,
      vix_zone: s.vix_zone,
      bucket: s.bucket,
      group: meta.group,
    }
  }

  // HK watchlist — long candidates → LONG, short → SHORT
  for (const r of hkWatchlist) {
    const dir = r.candidate_type === 'long' ? 'LONG' : 'SHORT'
    // If already exists from signals, enrich (don't override direction)
    if (dirSignalMap[r.symbol]) {
      dirSignalMap[r.symbol].rs_zscore = r.rs_zscore
      dirSignalMap[r.symbol].beta_vix = r.beta_vix
      dirSignalMap[r.symbol].beta_dxy = r.beta_dxy
      dirSignalMap[r.symbol].beta_group = r.beta_group
    } else {
      dirSignalMap[r.symbol] = {
        ticker: r.symbol,
        direction: dir,
        isHeld: false,
        price: null,
        vix_zone: 'hk',
        bucket: 'alpha',
        group: null,
        rs_zscore: r.rs_zscore,
        beta_vix: r.beta_vix,
        beta_dxy: r.beta_dxy,
        beta_group: r.beta_group,
      }
    }
  }

  // Portfolio — mark isHeld on existing items, create HOLD items for rest
  const portfolioTickers = new Set(positions.map(p => p.ticker))
  const posMap = {}
  for (const p of positions) {
    posMap[p.ticker] = p
    if (dirSignalMap[p.ticker]) {
      dirSignalMap[p.ticker].isHeld = true
      if (p.position_qty != null) dirSignalMap[p.ticker].qty = p.position_qty
    } else {
      dirSignalMap[p.ticker] = {
        ticker: p.ticker,
        direction: 'HOLD',
        isHeld: true,
        price: null,
        vix_zone: null,
        bucket: p.bucket || 'existing',
        group: null,
        qty: p.position_qty,
      }
    }
  }

  // Partition by direction
  const longItems = []
  const shortItems = []
  const holdItems = []

  for (const ticker in dirSignalMap) {
    const item = dirSignalMap[ticker]
    if (item.direction === 'LONG') longItems.push(item)
    else if (item.direction === 'SHORT') shortItems.push(item)
    else holdItems.push(item)
  }

  // Sort: by price descending for Long/Hold, ascending for Short
  longItems.sort((a, b) => (b.price ?? 0) - (a.price ?? 0))
  shortItems.sort((a, b) => (a.price ?? 0) - (b.price ?? 0))
  holdItems.sort((a, b) => (a.ticker || '').localeCompare(b.ticker || ''))

  const heldSet = new Set(positions.map(p => p.ticker))
  const hasAny = longItems.length > 0 || shortItems.length > 0 || holdItems.length > 0

  // ── Handlers ──
  const openChart = useCallback((symbol) => setChartSymbol(symbol), [])
  const closeChart = useCallback(() => setChartSymbol(null), [])

  // ── Main Dashboard ──
  return (
    <div className="min-h-screen bg-market-950 text-market-100">
      <div className="max-w-6xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold text-white">Dashboard</h1>
            <p className="text-sm text-market-400 mt-1">
              {longItems.length} long · {holdItems.length} hold · {shortItems.length} short
              {regime && <span className="ml-2">{regimeBadge(regime)}</span>}
            </p>
          </div>
          <a href="/" className="text-sm text-market-500 hover:text-market-300 transition-colors">← Home</a>
        </div>

        {loading && (
          <div className="text-center py-12 text-market-500">
            <div className="animate-spin inline-block w-6 h-6 border-2 border-market-600 border-t-blue-500 rounded-full mb-3" />
            <p>Loading...</p>
          </div>
        )}

        {!loading && !hasAny && (
          <div className="text-center py-12 border border-dashed border-market-700 rounded-lg">
            <p className="text-market-500">No signals or holdings yet. Data will appear here when available.</p>
          </div>
        )}

        {!loading && hasAny && (
          <>
            <Section title="Long" items={longItems} onChart={openChart} stockNames={stockNames} onRsInfo={() => setRsExplainer(true)} indicators={indicatorSignals} />
            <Section title="Hold" items={holdItems} onChart={openChart} stockNames={stockNames} onRsInfo={() => setRsExplainer(true)} indicators={indicatorSignals} />
            <Section title="Short" items={shortItems} onChart={openChart} stockNames={stockNames} onRsInfo={() => setRsExplainer(true)} indicators={indicatorSignals} />
          </>
        )}
      </div>

      {rsExplainer && <RsExplainerModal onClose={() => setRsExplainer(false)} />}
      {chartSymbol && <ChartModal key={chartSymbol} symbol={chartSymbol} onClose={closeChart} />}
    </div>
  )
}
