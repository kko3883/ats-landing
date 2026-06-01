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

function toTradingViewSymbol(symbol) {
  // TradingView accepts .HK suffix natively — more reliable than HKEX: prefix
  // e.g. "2382.HK", "9626.HK", "1810.HK" all resolve correctly
  if (symbol.endsWith('.HK')) {
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

  // Load TradingView script + create widget
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // Clean any leftover widget from previous mount
    container.innerHTML = ''
    if (widgetRef.current) {
      try { widgetRef.current.remove() } catch {}
      widgetRef.current = null
    }

    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/tv.js'
    script.async = true
    script.onload = () => {
      if (!container || !window.TradingView) return
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
    document.body.appendChild(script)

    return () => {
      // Cleanup
      if (widgetRef.current) {
        try { widgetRef.current.remove() } catch {}
        widgetRef.current = null
      }
      if (script.parentNode) script.parentNode.removeChild(script)
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

// ── Card Components ─────────────────────────────────────────────────────

function SignalCard({ signal, onChart, stockNames }) {
  const bucket = signal.bucket || 'alpha'
  const style = BUCKET_COLORS[bucket] || BUCKET_COLORS.alpha
  const meta = signal.signal_json || {}
  const ctx = meta.context || {}
  const ticker = signal.ticker
  const name = stockNames?.[ticker] || stockNames?.[`${ticker}.US`] || ''

  return (
    <div
      className={`${style.bg} ${style.border} border rounded-lg p-4 cursor-pointer transition-colors group hover:brightness-110`}
      onClick={() => onChart?.(ticker)}
    >
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <div>
            <span className="text-lg font-bold text-white">{ticker}</span>
            {name && <span className="block text-xs text-market-400 leading-tight">{name}</span>}
          </div>
          <svg className="w-4 h-4 text-market-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01" />
          </svg>
        </div>
        <span className={`text-xs font-mono px-2 py-0.5 rounded-full shrink-0 ${signal.direction === 'LONG' ? 'bg-emerald-800 text-emerald-200' : 'bg-red-800 text-red-200'}`}>
          {signal.direction}
        </span>
      </div>
      <div className="space-y-1 text-xs text-market-300">
        <div className="flex justify-between">
          <span>Bucket</span>
          <span className={style.text}>{bucket}</span>
        </div>
        {meta.price && (
          <div className="flex justify-between">
            <span>Price</span>
            <span className="text-white">${typeof meta.price === 'number' ? meta.price.toFixed(2) : meta.price}</span>
          </div>
        )}
        {meta.stop_loss && (
          <div className="flex justify-between">
            <span>Stop Loss</span>
            <span className="text-red-400">${typeof meta.stop_loss === 'number' ? meta.stop_loss.toFixed(2) : meta.stop_loss}</span>
          </div>
        )}
        {/* Indicator pills */}
        <div className="flex flex-wrap gap-1.5 mt-2">
          {ctx.breakout && <span className="text-xs bg-blue-800 text-blue-200 px-1.5 py-0.5 rounded">Breakout</span>}
          {ctx.sma_crossover && <span className="text-xs bg-purple-800 text-purple-200 px-1.5 py-0.5 rounded">Golden X</span>}
          {ctx.rsi_lt === true && <span className="text-xs bg-orange-800 text-orange-200 px-1.5 py-0.5 rounded">Oversold</span>}
          {ctx.rsi_gt === true && <span className="text-xs bg-rose-800 text-rose-200 px-1.5 py-0.5 rounded">Overbought</span>}
          {ctx.price_above_sma && <span className="text-xs bg-emerald-800 text-emerald-200 px-1.5 py-0.5 rounded">Above SMA</span>}
          {ctx.price_below_sma && <span className="text-xs bg-red-800 text-red-200 px-1.5 py-0.5 rounded">Below SMA</span>}
          {ctx.pullback === true && <span className="text-xs bg-cyan-800 text-cyan-200 px-1.5 py-0.5 rounded">Pullback</span>}
        </div>
        {meta.strategy_name && (
          <div className="flex justify-between pt-1">
            <span className="text-market-500 italic">{meta.strategy_name.replace(/_/g, ' ')}</span>
          </div>
        )}
      </div>
    </div>
  )
}

function HkCard({ symbol, candidate_type, rs_zscore, beta_vix, beta_dxy, beta_group, onChart, stockNames, onRsInfo }) {
  const isLong = candidate_type === 'long'
  const name = stockNames?.[symbol] || ''
  return (
    <div
      className={`${isLong ? 'bg-emerald-900/20 border-emerald-700/40 hover:bg-emerald-900/40' : 'bg-red-900/20 border-red-700/40 hover:bg-red-900/40'} border rounded-lg p-3 cursor-pointer transition-colors group`}
      onClick={() => onChart?.(symbol)}
    >
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2 min-w-0">
          <div className="min-w-0">
            <span className="font-bold text-white">{symbol}</span>
            {name && <span className="block text-xs text-market-400 truncate">{name}</span>}
          </div>
          <svg className="w-3.5 h-3.5 text-market-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01" />
          </svg>
        </div>
        <span className={`text-xs font-mono px-1.5 py-0.5 rounded shrink-0 ${isLong ? 'bg-emerald-800 text-emerald-200' : 'bg-red-800 text-red-200'}`}>
          {isLong ? 'LONG' : 'SHORT'}
        </span>
      </div>
      <div className="text-xs text-market-400 space-y-0.5">
        <div className="flex justify-between items-center">
          <span className="flex items-center gap-1">
            RS Z-Score
            <button onClick={(e) => { e.stopPropagation(); onRsInfo?.(); }} className="text-market-500 hover:text-market-300">
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </button>
          </span>
          <span className={isLong ? 'text-emerald-300' : 'text-red-300'}>{rs_zscore > 0 ? '+' : ''}{typeof rs_zscore === 'number' ? rs_zscore.toFixed(3) : rs_zscore}</span>
        </div>
        {beta_vix != null && (
          <div className="flex justify-between">
            <span>VIX Beta</span>
            <span className={beta_vix < 0 ? 'text-rose-300' : 'text-emerald-300'}>{beta_vix > 0 ? '+' : ''}{typeof beta_vix === 'number' ? beta_vix.toFixed(3) : beta_vix}</span>
          </div>
        )}
        {beta_dxy != null && (
          <div className="flex justify-between">
            <span>DXY Beta</span>
            <span className={beta_dxy < 0 ? 'text-rose-300' : 'text-emerald-300'}>{beta_dxy > 0 ? '+' : ''}{typeof beta_dxy === 'number' ? beta_dxy.toFixed(3) : beta_dxy}</span>
          </div>
        )}
        {beta_group && (
          <div className="flex justify-between">
            <span>Beta Group</span>
            <span className="text-market-300">{beta_group.replace('group_', '')}</span>
          </div>
        )}
      </div>
    </div>
  )
}

function BucketSection({ title, signals, style, onChart, stockNames }) {
  if (!signals || signals.length === 0) return null
  return (
    <div className="mb-6">
      <h3 className={`text-sm font-semibold uppercase tracking-wider mb-3 ${style.text}`}>{title} ({signals.length})</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {signals.map(s => <SignalCard key={s.id} signal={s} onChart={onChart} stockNames={stockNames} />)}
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
              {signals.length} signals · {hkWatchlist.length} HK watchlist entries
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
                      <HkCard key={`${r.id}-${i}`} {...r} onChart={openChart} stockNames={stockNames} onRsInfo={() => setRsExplainer('rs')} />
                    ))}
                  </div>
                </div>

                <div>
                  <h3 className="text-sm font-semibold uppercase tracking-wider text-red-400 mb-3">
                    Top Short Candidates ({hkShort.length})
                  </h3>
                  <div className="space-y-2">
                    {hkShort.map((r, i) => (
                      <HkCard key={`${r.id}-${i}`} {...r} onChart={openChart} stockNames={stockNames} onRsInfo={() => setRsExplainer('rs')} />
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
