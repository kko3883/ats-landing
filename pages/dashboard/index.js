import { useEffect, useState } from 'react'
import supabase from '../../lib/supabase'

const BUCKET_LABELS = { base_yield: 'Base Yield', alpha: 'Alpha', convexity: 'Convexity' }
const BUCKET_COLORS = { base_yield: 'text-blue-400', alpha: 'text-green-400', convexity: 'text-purple-400' }
const DIR_COLORS = { LONG: 'bg-green-900/40 text-green-400', SHORT: 'bg-red-900/40 text-red-400', NEUTRAL: 'bg-yellow-900/40 text-yellow-400' }

// ── Conflict resolution ──
const ALIGNMENT = {
  GO: { badge: '🟢 GO', border: 'border-green-500/50', bg: 'bg-green-900/20', label: 'Screener + Indicators agree' },
  WATCH: { badge: '🟡 WATCH', border: 'border-yellow-500/50', bg: 'bg-yellow-900/20', label: 'One neutral, one directional' },
  WAIT: { badge: '🔴 WAIT', border: 'border-red-500/50', bg: 'bg-red-900/20', label: 'Opposing signals — wait' },
  LOADING: { badge: '⏳ —', border: 'border-market-800/30', bg: '', label: 'No indicator data yet' },
}

// ── Regime banner ──
const REGIME_STYLE = {
  risk_on: { border: 'border-green-500/60', bg: 'bg-green-900/20', text: 'text-green-300', dot: 'bg-green-400', label: 'Risk-On' },
  choppy: { border: 'border-yellow-500/60', bg: 'bg-yellow-900/20', text: 'text-yellow-300', dot: 'bg-yellow-400', label: 'Choppy' },
  risk_off: { border: 'border-red-500/60', bg: 'bg-red-900/20', text: 'text-red-300', dot: 'bg-red-400', label: 'Risk-Off' },
  crisis: { border: 'border-red-500/60', bg: 'bg-red-900/30', text: 'text-red-200', dot: 'bg-red-500', label: 'Crisis' },
}

// ── Indicator signal → direction ──
function indicatorToDirection(signal) {
  if (signal === 'strong_buy' || signal === 'buy') return 'LONG'
  if (signal === 'strong_sell' || signal === 'sell') return 'SHORT'
  return 'NEUTRAL'
}

function getAlignment(signalDirection, indicatorDirection) {
  if (!indicatorDirection) return 'LOADING'
  if (signalDirection === indicatorDirection) return 'GO'
  if (indicatorDirection === 'NEUTRAL' || signalDirection === 'NEUTRAL') return 'WATCH'
  return 'WAIT'
}

function TimeAgo({ ts }) {
  const [label, setLabel] = useState('')
  useEffect(() => {
    const update = () => {
      const secs = (Date.now() - new Date(ts).getTime()) / 1000
      if (secs < 60) setLabel('just now')
      else if (secs < 3600) setLabel(`${Math.floor(secs / 60)}m ago`)
      else if (secs < 86400) setLabel(`${Math.floor(secs / 3600)}h ago`)
      else setLabel(`${Math.floor(secs / 86400)}d ago`)
    }
    update()
    const id = setInterval(update, 30000)
    return () => clearInterval(id)
  }, [ts])
  return <span className="text-market-500 text-xs">{label}</span>
}

// ── Entry Card (expandable) ──
function EntryCard({ indicator }) {
  if (!indicator) return null
  const price = indicator.current_price
  const atr = indicator.atr_value
  const atrPct = price > 0 ? (atr / price * 100).toFixed(2) : '?'
  const stop = indicator.stop_loss
  const tp = indicator.take_profit
  const rr = indicator.risk_reward
  const size = indicator.suggested_size
  const zoneLow = indicator.entry_zone_low
  const zoneHigh = indicator.entry_zone_high
  const sig = indicator.composite_signal

  const isLong = sig === 'strong_buy' || sig === 'buy'
  const isShort = sig === 'strong_sell' || sig === 'sell'
  if (!isLong && !isShort) return null  // no entry plan for hold/neutral

  const stopPct = price > 0 ? ((isLong ? (price - stop) : (stop - price)) / price * 100).toFixed(2) : '?'
  const tpPct = price > 0 ? ((isLong ? (tp - price) : (price - tp)) / price * 100).toFixed(2) : '?'
  const sizeValue = size ? `$${(size * price).toLocaleString()}` : '—'

  return (
    <div className="mt-3 pt-3 border-t border-market-800/40 grid grid-cols-2 sm:grid-cols-4 gap-3 text-[11px]">
      <div>
        <div className="text-market-500 mb-0.5">Stop Loss</div>
        <div className="text-red-400 font-mono">${stop?.toFixed(2) || '—'}</div>
        <div className="text-market-600 text-[10px]">{stopPct !== '?' ? `-${stopPct}%` : ''}</div>
      </div>
      <div>
        <div className="text-market-500 mb-0.5">Take Profit</div>
        <div className="text-green-400 font-mono">${tp?.toFixed(2) || '—'}</div>
        <div className="text-market-600 text-[10px]">{tpPct !== '?' ? `+${tpPct}%` : ''}</div>
      </div>
      <div>
        <div className="text-market-500 mb-0.5">Entry Zone</div>
        <div className="text-market-300 font-mono">${zoneLow?.toFixed(2) || price?.toFixed(2)} – ${zoneHigh?.toFixed(2) || price?.toFixed(2)}</div>
      </div>
      <div>
        <div className="text-market-500 mb-0.5">ATR: {atr?.toFixed(2)} ({atrPct}%)</div>
        <div className="text-market-300 font-mono">R:R 1:{rr || '?'}</div>
        <div className="text-market-500 text-[10px]">Size: {size || '—'} ({sizeValue})</div>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [connected, setConnected] = useState(false)
  const [signals, setSignals] = useState([])
  const [indicatorMap, setIndicatorMap] = useState({})     // ticker → composite_signal
  const [indicatorDetail, setIndicatorDetail] = useState({}) // ticker → full indicator row
  const [regime, setRegime] = useState(null)
  const [loading, setLoading] = useState(true)
  const [bucketCounts, setBucketCounts] = useState({})
  const [lastUpdate, setLastUpdate] = useState(null)
  const [expanded, setExpanded] = useState({})              // which signal cards are expanded
  const [positions, setPositions] = useState([])            // portfolio positions
  const [heldTickers, setHeldTickers] = useState(new Set()) // tickers already held (for dedup)

  useEffect(() => {
    if (!supabase) {
      setConnected(false)
      setLoading(false)
      return
    }
    setConnected(true)

    // Fetch existing signals (pending + executed only, hide expired)
    supabase
      .from('signals')
      .select('*')
      .or('status.eq.pending,status.is.null')
      .order('id', { ascending: false })
      .limit(50)
      .then(({ data, error }) => {
        if (error) console.error('Fetch error:', error)
        else {
          setSignals(data || [])
          setLastUpdate(new Date())
        }
        setLoading(false)
      })

    // Fetch latest indicator signals (full row for entry cards)
    supabase
      .from('indicator_signals')
      .select('ticker, composite_signal, calculated_at, atr_value, stop_loss, take_profit, current_price, entry_zone_low, entry_zone_high, risk_reward, suggested_size, bb_upper, bb_lower, bb_mid')
      .order('calculated_at', { ascending: false })
      .limit(200)
      .then(({ data, error }) => {
        if (error) {
          console.error('Indicator fetch error:', error)
          return
        }
        const compMap = {}
        const detailMap = {}
        for (const row of data || []) {
          if (!compMap[row.ticker]) {
            compMap[row.ticker] = row.composite_signal
            detailMap[row.ticker] = row
          }
        }
        setIndicatorMap(compMap)
        setIndicatorDetail(detailMap)
      })

    // Fetch latest regime
    supabase
      .from('regime')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(1)
      .then(({ data, error }) => {
        if (error) console.error('Regime fetch error:', error)
        else if (data && data.length > 0) {
          setRegime(data[0])
        }
      })

    // Fetch portfolio positions
    supabase
      .from('portfolio')
      .select('*')
      .order('id', { ascending: true })
      .then(({ data, error }) => {
        if (error) {
          console.error('Portfolio fetch error:', error)
          return
        }
        if (data && data.length > 0) {
          setPositions(data)
          // Build held ticker set for dedup (strip suffixes like .US/.HK)
          const held = new Set()
          for (const p of data) {
            const clean = p.ticker?.replace(/\.(US|HK)$/, '').toUpperCase()
            if (clean) held.add(clean)
            if (p.ticker) held.add(p.ticker.toUpperCase())
          }
          setHeldTickers(held)
        }
      })

    // Subscribe to new signals in realtime
    const signalChannel = supabase
      .channel('signals')
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'signals' },
        (payload) => {
          setSignals(prev => [payload.new, ...prev])
          setLastUpdate(new Date())
        }
      )
      .subscribe()

    // Subscribe to portfolio changes (synced by cron)
    const portfolioChannel = supabase
      .channel('portfolio')
      .on('postgres_changes',
        { event: '*', schema: 'public', table: 'portfolio' },
        () => {
          // Refetch on any change
          supabase.from('portfolio').select('*').order('id', { ascending: true })
            .then(({ data }) => {
              if (data) {
                setPositions(data)
                const held = new Set()
                for (const p of data) {
                  const clean = p.ticker?.replace(/\.(US|HK)$/, '').toUpperCase()
                  if (clean) held.add(clean)
                  if (p.ticker) held.add(p.ticker.toUpperCase())
                }
                setHeldTickers(held)
              }
            })
        }
      )
      .subscribe()

    return () => {
      supabase.removeChannel(signalChannel)
      supabase.removeChannel(portfolioChannel)
    }
  }, [])

  // Compute bucket counts
  useEffect(() => {
    const counts = {}
    for (const s of signals) {
      const b = s.bucket || 'other'
      counts[b] = (counts[b] || 0) + 1
      counts._total = (counts._total || 0) + 1
    }
    setBucketCounts(counts)
  }, [signals])

  // Sort signals: GO → WATCH → WAIT → LOADING
  const sortOrder = { GO: 0, WATCH: 1, WAIT: 2, LOADING: 3 }
  const sortedSignals = [...signals].sort((a, b) => {
    const indA = indicatorMap[a.ticker]
    const indB = indicatorMap[b.ticker]
    const alignA = getAlignment(a.direction, indicatorToDirection(indA))
    const alignB = getAlignment(b.direction, indicatorToDirection(indB))
    return sortOrder[alignA] - sortOrder[alignB]
  })

  const toggleExpand = (id) => {
    setExpanded(prev => ({ ...prev, [id]: !prev[id] }))
  }

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

  return (
    <div className="min-h-screen bg-market-950 text-market-100">
      <div className="max-w-6xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-4">
            <h1 className="text-2xl font-bold text-white">Dashboard</h1>
            <a href="/equity" className="text-xs px-3 py-1 rounded-full border border-blue-500/40 text-blue-400 hover:bg-blue-500/10 transition-colors">
              📊 Equity Engine →
            </a>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
            <span className="text-xs text-market-400">Live</span>
            {lastUpdate && <TimeAgo ts={lastUpdate} />}
          </div>
        </div>

        {/* ── Regime Banner ── */}
        {regime && (
          <div className={`mb-6 rounded-xl border ${REGIME_STYLE[regime.regime_name]?.border || 'border-market-800/40'} ${REGIME_STYLE[regime.regime_name]?.bg || 'bg-market-900/30'} p-4`}>
            <div className="flex items-center gap-3 mb-1">
              <div className={`w-2 h-2 rounded-full ${REGIME_STYLE[regime.regime_name]?.dot || 'bg-market-400'} animate-pulse`} />
              <span className={`text-sm font-semibold ${REGIME_STYLE[regime.regime_name]?.text || 'text-market-200'}`}>
                {REGIME_STYLE[regime.regime_name]?.label || regime.regime_name}
              </span>
              <span className="text-xs text-market-500 ml-1">VIX {regime.vix_level}</span>
            </div>
            <p className="text-xs text-market-400 mb-2">{regime.description}</p>
            {regime.activated_groups && regime.activated_groups.length > 0 && (
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] text-market-500 uppercase tracking-wider">Prioritize:</span>
                {regime.activated_groups.map(g => (
                  <span key={g} className="text-[10px] px-2 py-0.5 rounded-full bg-market-800/50 text-market-300 font-mono">
                    {g.replace(/_/g, ' ')}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Bucket cards */}
        {bucketCounts._total > 0 && (
          <div className="grid grid-cols-3 gap-4 mb-8">
            {['base_yield', 'alpha', 'convexity'].map(b => (
              <div key={b} className="rounded-xl border border-market-800/40 bg-market-900/50 backdrop-blur-sm p-4">
                <div className="text-xs text-market-500 mb-1">{BUCKET_LABELS[b] || b}</div>
                <div className={`text-2xl font-bold ${BUCKET_COLORS[b] || 'text-white'}`}>
                  {bucketCounts[b] || 0}
                </div>
                <div className="text-[10px] text-market-600">active signals</div>
              </div>
            ))}
          </div>
        )}

        {/* ── Portfolio Section ── */}
        {positions.length > 0 && (() => {
          const totalVal = positions.reduce((sum, p) => sum + (p.market_value || 0), 0)
          const totalPnl = positions.reduce((sum, p) => sum + (p.unrealized_pnl || 0), 0)

          // VIX zone concentration
          const zoneVal = {}
          for (const p of positions) {
            const z = p.vix_zone || 'unknown'
            zoneVal[z] = (zoneVal[z] || 0) + (p.market_value || 0)
          }

          return (
            <div className="mb-8 rounded-xl border border-market-800/40 bg-market-900/30 p-5">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold text-market-300 uppercase tracking-wider">
                  Portfolio ({positions.length} positions)
                </h2>
                <div className="flex items-center gap-4 text-xs">
                  <span className="text-market-400">
                    Total: <span className="text-white font-mono">${totalVal.toLocaleString()}</span>
                  </span>
                  <span className={totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}>
                    P&L: <span className="font-mono">${totalPnl.toLocaleString(undefined, { signDisplay: 'always' })}</span>
                  </span>
                </div>
              </div>

              {/* Positions table */}
              <div className="overflow-x-auto mb-3">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-market-500 border-b border-market-800/30">
                      <th className="text-left py-1.5 pr-3">Ticker</th>
                      <th className="text-right py-1.5 px-3">Qty</th>
                      <th className="text-right py-1.5 px-3">Value</th>
                      <th className="text-right py-1.5 px-3">Alloc</th>
                      <th className="text-right py-1.5 px-3">P&L</th>
                      <th className="text-left py-1.5 pl-3">Zone</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map(p => (
                      <tr key={p.id} className="border-b border-market-800/20 hover:bg-market-800/20 transition-colors">
                        <td className="py-2 pr-3 font-mono text-white">{p.ticker}</td>
                        <td className="py-2 px-3 text-right font-mono text-market-300">{p.quantity}</td>
                        <td className="py-2 px-3 text-right font-mono text-market-300">
                          ${(p.market_value || 0).toLocaleString()}
                        </td>
                        <td className="py-2 px-3 text-right font-mono text-market-400">
                          {p.allocation_pct != null ? `${p.allocation_pct}%` : '—'}
                        </td>
                        <td className={`py-2 px-3 text-right font-mono ${(p.unrealized_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {p.unrealized_pnl != null ? `$${p.unrealized_pnl.toLocaleString(undefined, { signDisplay: 'always' })}` : '—'}
                        </td>
                        <td className="py-2 pl-3">
                          {p.vix_zone ? (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-market-800/50 text-market-400">
                              {p.vix_zone.replace(/_/g, ' ')}
                            </span>
                          ) : (
                            <span className="text-market-600">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Concentration gauge */}
              <div className="flex items-center gap-2 flex-wrap pt-2 border-t border-market-800/30">
                <span className="text-[10px] text-market-500 uppercase tracking-wider">VIX Zone Exposure:</span>
                {Object.entries(zoneVal)
                  .sort((a, b) => b[1] - a[1])
                  .map(([zone, val]) => {
                    const pct = totalVal > 0 ? (val / totalVal * 100) : 0
                    const isWarn = pct > 40
                    return (
                      <span
                        key={zone}
                        className={`text-[10px] px-2 py-0.5 rounded-full font-mono ${
                          isWarn
                            ? 'bg-red-900/40 text-red-300 border border-red-500/40'
                            : 'bg-market-800/50 text-market-300'
                        }`}
                        title={isWarn ? `⚠ Over-concentrated: ${pct.toFixed(0)}% in ${zone}` : undefined}
                      >
                        {zone.replace(/_/g, ' ')} {pct.toFixed(0)}%{isWarn ? ' ⚠' : ''}
                      </span>
                    )
                  })}
              </div>
            </div>
          )
        })()}

        {/* Loading state */}
        {loading && (
          <div className="text-center py-12">
            <div className="animate-spin w-6 h-6 border-2 border-market-500 border-t-green-400 rounded-full mx-auto mb-3" />
            <p className="text-market-500 text-sm">Loading signals...</p>
          </div>
        )}

        {/* Empty state */}
        {!loading && signals.length === 0 && (
          <div className="text-center py-16">
            <div className="text-3xl mb-3">📡</div>
            <p className="text-market-400">No signals yet</p>
            <p className="text-market-600 text-sm mt-1">
              Signals will appear here once the engine runs and generates them.
            </p>
          </div>
        )}

        {/* Signal list with expandable entry cards */}
        {signals.length > 0 && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-market-300 uppercase tracking-wider">
                Signal Feed ({signals.length})
              </h2>
              <span className="text-[10px] text-market-600">Click card for entry plan</span>
            </div>
            <div className="space-y-2">
              {sortedSignals.map((s) => {
                const indSignal = indicatorMap[s.ticker]
                const indDir = indicatorToDirection(indSignal)
                const alignment = getAlignment(s.direction, indDir)
                const a = ALIGNMENT[alignment]
                const detail = indicatorDetail[s.ticker]
                const isExpanded = expanded[s.id]

                // Check if already held (dedup)
                const cleanTicker = s.ticker?.replace(/\.(US|HK)$/, '').toUpperCase()
                const isHeld = (cleanTicker && heldTickers.has(cleanTicker)) || heldTickers.has(s.ticker?.toUpperCase())

                return (
                  <div key={s.id}>
                    <div
                      onClick={() => toggleExpand(s.id)}
                      className={`flex items-center justify-between px-4 py-3 rounded-xl border ${a.border} ${a.bg || 'bg-market-900/30'} hover:bg-market-800/30 transition-colors cursor-pointer ${isHeld ? 'opacity-60' : ''}`}
                    >
                      <div className="flex items-center gap-4">
                        <span className="text-sm font-mono font-semibold text-white w-16">{s.ticker}</span>
                        {isHeld && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded font-mono bg-blue-900/30 text-blue-400 border border-blue-500/30" title="Already in portfolio">
                            HELD
                          </span>
                        )}
                        <span className={`text-xs px-2 py-0.5 rounded font-mono ${DIR_COLORS[s.direction] || 'text-market-400'}`}>
                          {s.direction || '—'}
                        </span>
                        <span className={`text-xs font-mono ${BUCKET_COLORS[s.bucket] || 'text-market-400'}`}>
                          {BUCKET_LABELS[s.bucket] || s.bucket}
                        </span>
                        {s.vix_zone && (
                          <span className="text-[10px] text-market-500 bg-market-800/50 px-1.5 py-0.5 rounded">
                            {s.vix_zone.replace('_', ' ')}
                          </span>
                        )}
                        {indSignal && (
                          <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                            indSignal === 'strong_buy' || indSignal === 'buy' ? 'text-green-400 bg-green-900/30' :
                            indSignal === 'strong_sell' || indSignal === 'sell' ? 'text-red-400 bg-red-900/30' :
                            'text-market-500 bg-market-800/30'
                          }`}>
                            {indSignal.replace(/_/g, ' ')}
                          </span>
                        )}
                        {/* Expand indicator */}
                        {detail?.atr_value && (
                          <span className="text-[10px] text-market-600">
                            {isExpanded ? '▲' : '▼'}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="text-[10px] font-mono text-market-400" title={a.label}>
                          {a.badge}
                        </span>
                        {s.signal_json?.stop_loss && (
                          <span className="text-[10px] text-red-400/60 font-mono">
                            SL {s.signal_json.stop_loss.toFixed(2)}
                          </span>
                        )}
                        <TimeAgo ts={s.created_at} />
                      </div>
                    </div>
                    {/* Expanded entry card */}
                    {isExpanded && <EntryCard indicator={detail} />}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}