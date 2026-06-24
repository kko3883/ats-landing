import { useEffect, useState, useRef, useCallback } from 'react'
import supabase from '../../lib/supabase'

// ── Formatters ──────────────────────────────────────────────────────────

function formatMoney(v) {
  if (v == null || v === undefined) return '—'
  return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function formatPct(v) {
  if (v == null || v === undefined) return '—'
  const pct = (Number(v) * 100).toFixed(2)
  return (Number(v) >= 0 ? '+' : '') + pct + '%'
}
function formatPctV(v) {
  if (v == null || v === undefined) return '—'
  return (Number(v) >= 0 ? '+' : '') + Number(v).toFixed(2) + '%'
}

function timeAgo(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  const now = new Date()
  const sec = Math.floor((now - d) / 1000)
  if (sec < 3) return 'now'
  if (sec < 60) return sec + 's ago'
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago'
  return d.toLocaleTimeString()
}

// ── SVG sparkline for equity ────────────────────────────────────────────

function EquitySparkline({ data }) {
  if (!data || data.length < 2) return null
  const w = 400, h = 60, pad = 2
  const vals = data.map(d => d.equity).filter(v => v != null)
  if (vals.length < 2) return null
  const min = Math.min(...vals), max = Math.max(...vals)
  const range = max - min || 1
  const points = vals.map((v, i) => {
    const x = pad + (i / (vals.length - 1)) * (w - pad * 2)
    const y = h - pad - ((v - min) / range) * (h - pad * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const isUp = vals[vals.length - 1] >= vals[0]
  const color = isUp ? '#3fb950' : '#f85149'
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}

// ── Mini monitor chart (price + prob overlay) ───────────────────────────

function SymbolMonitorChart({ snapshots, symbol }) {
  if (!snapshots || snapshots.length < 2) {
    return <div style={{ fontSize: 11, color: '#8b949e', padding: 10 }}>Not enough data points for chart yet.</div>
  }
  const w = 500, h = 120, padL = 30, padR = 30, padT = 10, padB = 20
  const plotW = w - padL - padR
  const plotH = h - padT - padB

  const prices = snapshots.map(s => s.price).filter(v => v != null)
  const probs = snapshots.map(s => s.prob != null ? s.prob * 100 : null)

  const pMin = Math.min(...prices), pMax = Math.max(...prices)
  const pRange = pMax - pMin || 1

  const pricePoints = snapshots.map((s, i) => {
    if (s.price == null) return null
    const x = padL + (i / Math.max(snapshots.length - 1, 1)) * plotW
    const y = padT + plotH - ((s.price - pMin) / pRange) * plotH
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).filter(Boolean).join(' ')

  const validProbs = probs.filter(v => v != null)
  const probPoints = snapshots.map((s, i) => {
    if (s.prob == null) return null
    const x = padL + (i / Math.max(snapshots.length - 1, 1)) * plotW
    const y = padT + plotH - ((s.prob * 100) / 100) * plotH
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).filter(Boolean).join(' ')

  const thresholdY = padT + plotH - (0.65 * plotH)

  return (
    <div>
      <div style={{ fontSize: 10, color: '#8b949e', marginBottom: 4, display: 'flex', gap: 16 }}>
        <span style={{ color: '#58a6ff' }}>— Price</span>
        <span style={{ color: '#d2a8ff' }}>— XGBoost Prob</span>
        <span style={{ color: '#d2991d' }}>- - Threshold (0.65)</span>
      </div>
      <svg width={w} height={h} style={{ display: 'block', background: '#0d1117', borderRadius: 4 }}>
        {/* Threshold line */}
        <line x1={padL} y1={thresholdY} x2={w - padR} y2={thresholdY} stroke="#d2991d" strokeWidth="0.5" strokeDasharray="4,3" />
        {/* Price line */}
        <polyline points={pricePoints} fill="none" stroke="#58a6ff" strokeWidth="1.5" />
        {/* Probability line */}
        <polyline points={probPoints} fill="none" stroke="#d2a8ff" strokeWidth="1.5" />
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: '#8b949e', marginTop: 2 }}>
        <span>{snapshots[0]?.ts?.slice(11, 19) || '—'}</span>
        <span>{snapshots[snapshots.length - 1]?.ts?.slice(11, 19) || '—'}</span>
      </div>
    </div>
  )
}

// ── Gauge component ─────────────────────────────────────────────────────

function Gauge({ value, max, color, label, formatVal }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100))
  return (
    <div style={{ textAlign: 'center', flex: 1 }}>
      <div style={{ fontSize: 9, color: '#8b949e', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, color: color || '#c9d1d9' }}>
        {formatVal ? formatVal(value) : (value != null ? value.toFixed(2) : '—')}
      </div>
      <div style={{ height: 3, background: '#30363d', borderRadius: 2, overflow: 'hidden', marginTop: 2 }}>
        <div style={{ height: '100%', borderRadius: 2, width: pct + '%', background: color || '#58a6ff', transition: 'width 0.3s' }} />
      </div>
    </div>
  )
}

// ── Main Dashboard ──────────────────────────────────────────────────────

export default function EquityDashboard() {
  const [state, setState] = useState(null)
  const [connected, setConnected] = useState(false)
  const [lastUpdate, setLastUpdate] = useState(null)
  const [activities, setActivities] = useState([])
  const [shortlist, setShortlist] = useState([])
  const [shortlistOpen, setShortlistOpen] = useState(false)
  const [shortlistLoading, setShortlistLoading] = useState(false)
  const [equityHistory, setEquityHistory] = useState([])
  const [expandedSymbol, setExpandedSymbol] = useState(null)
  const [activeTab, setActiveTab] = useState('activity') // activity | shortlist | positions
  const [shortlistFilter, setShortlistFilter] = useState('all') // all | approved | ready | entered
  const activityEndRef = useRef(null)

  // ── Subscriptions & data fetching ──────────────────────────────────

  useEffect(() => {
    if (!supabase) { setConnected(false); return }
    setConnected(true)

    // Fetch initial state
    const fetchState = () => {
      supabase
        .from('equity_state')
        .select('*')
        .order('snapshot_at', { ascending: false })
        .limit(1)
        .then(({ data, error }) => {
          if (error) { console.error('Equity state fetch error:', error); return }
          if (data && data.length > 0) {
            setState(data[0])
            setLastUpdate(new Date())
          }
        })
    }
    fetchState()

    // Fetch equity history (last 200 snapshots)
    supabase
      .from('equity_state')
      .select('snapshot_at, equity')
      .order('snapshot_at', { ascending: false })
      .limit(200)
      .then(({ data, error }) => {
        if (!error && data) setEquityHistory(data.reverse())
      })

    // Fetch initial activities
    supabase
      .from('equity_activity')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(100)
      .then(({ data, error }) => {
        if (!error && data) setActivities(data.reverse())
      })

    // Realtime: equity_state
    const stateChannel = supabase
      .channel('equity_state')
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'equity_state' },
        (payload) => {
          setState(payload.new)
          setLastUpdate(new Date())
          // Append to equity history
          setEquityHistory(prev => {
            const next = [...prev, { snapshot_at: payload.new.snapshot_at, equity: payload.new.equity }]
            if (next.length > 200) return next.slice(-200)
            return next
          })
        }
      )
      .subscribe()

    // Realtime: equity_activity (live feed)
    let actChannel
    supabase
      .channel('equity_activity')
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'equity_activity' },
        (payload) => {
          setActivities(prev => {
            const next = [...prev, payload.new]
            if (next.length > 200) return next.slice(-200)
            return next
          })
        }
      )
      .subscribe((status, err) => {
        if (status === 'SUBSCRIBED') actChannel = status
      })

    // Poll fallback (every 5s)
    const poll = setInterval(fetchState, 5000)

    return () => {
      supabase.removeChannel(stateChannel)
      clearInterval(poll)
    }
  }, [])

  // Auto-scroll activity feed
  useEffect(() => {
    if (activityEndRef.current) {
      activityEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [activities.length])

  // ── Shortlist fetch (on-demand) ─────────────────────────────────────

  const fetchShortlist = useCallback(() => {
    if (!supabase) return
    setShortlistLoading(true)
    supabase
      .from('equity_shortlist')
      .select('*')
      .order('xgb_prob', { ascending: false })
      .then(({ data, error }) => {
        if (!error && data) setShortlist(data)
        setShortlistLoading(false)
      })
  }, [])

  useEffect(() => {
    if (shortlistOpen && shortlist.length === 0) {
      fetchShortlist()
    }
  }, [shortlistOpen, shortlist.length, fetchShortlist])

  // Auto-load shortlist on page load so user sees data immediately
  useEffect(() => {
    if (connected && shortlist.length === 0) {
      fetchShortlist()
    }
  }, [connected, shortlist.length, fetchShortlist])

  // Auto-refresh shortlist every 5 min while page is open
  useEffect(() => {
    if (!connected) return
    const timer = setInterval(fetchShortlist, 300000) // 5 min
    return () => clearInterval(timer)
  }, [connected, fetchShortlist])

  // ── Derived data ────────────────────────────────────────────────────

  if (!connected) {
    return (
      <div style={{ minHeight: '100vh', background: '#0d1117', color: '#c9d1d9', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ textAlign: 'center', maxWidth: 400, padding: 20 }}>
          <div style={{ fontSize: 40, marginBottom: 12 }}>🔒</div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: '#fff', marginBottom: 8 }}>Equity Dashboard</h1>
          <p style={{ color: '#8b949e', marginBottom: 16 }}>
            Set <code style={{ color: '#3fb950', background: '#161b22', padding: '2px 6px', borderRadius: 4, fontSize: 12 }}>NEXT_PUBLIC_SUPABASE_URL</code> and{' '}
            <code style={{ color: '#3fb950', background: '#161b22', padding: '2px 6px', borderRadius: 4, fontSize: 12 }}>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> to enable.
          </p>
          <a href="/" style={{ color: '#58a6ff' }}>← Back to Home</a>
        </div>
      </div>
    )
  }

  const s = state || {}
  const equity = s.equity || 100000
  const startEquity = s.starting_equity || 100000
  const dailyPnl = (equity - startEquity)
  const dailyPnlPct = startEquity > 0 ? (equity / startEquity - 1) : 0
  const posCount = s.position_count || 0
  const totalTrades = s.trade_count || 0
  const paused = s.paused
  const pauseReason = s.pause_reason || ''
  const regime = s.regime || 'unknown'
  const layer1Approved = s.layer1_approved || 0
  const layer2Signals = s.layer2_signals || 0
  const layer2Features = s.layer2_features || 0
  const layer3Exits = s.layer3_exits || 0
  const universeCount = s.universe_count || 50

  // Parse positions
  let positions = {}
  try {
    positions = typeof s.positions_json === 'string' ? JSON.parse(s.positions_json) : (s.positions_json || {})
  } catch { positions = {} }
  const posKeys = Object.keys(positions)

  // Shortlist breakdown
  const readyCount = shortlist.filter(r => r.status === 'ready').length
  const enteredCount = shortlist.filter(r => r.status === 'entered').length
  const blockedCount = shortlist.filter(r => r.status !== 'ready' && r.status !== 'entered').length
  const universeSize = universeCount

  const regimeStyles = {
    risk_on: { border: '1px solid rgba(63,185,80,0.4)', bg: 'rgba(63,185,80,0.1)', text: '#3fb950', dot: '#3fb950', label: 'Risk-On', icon: '🟢' },
    choppy: { border: '1px solid rgba(210,153,29,0.4)', bg: 'rgba(210,153,29,0.1)', text: '#d2991d', dot: '#d2991d', label: 'Choppy', icon: '🟡' },
    risk_off: { border: '1px solid rgba(248,81,73,0.4)', bg: 'rgba(248,81,73,0.1)', text: '#f85149', dot: '#f85149', label: 'Risk-Off', icon: '🔴' },
    crisis: { border: '1px solid rgba(248,81,73,0.5)', bg: 'rgba(248,81,73,0.15)', text: '#f85149', dot: '#f85149', label: 'Crisis', icon: '⚠️' },
  }
  const rs = regimeStyles[regime] || regimeStyles.choppy

  const eventTypeStyles = {
    signal_fired: { color: '#d2a8ff', icon: '⚡' },
    entry_blocked: { color: '#d2991d', icon: '⛔' },
    entry_confirmed: { color: '#3fb950', icon: '▶' },
    exit_triggered: { color: '#f85149', icon: '◀' },
    regime_change: { color: '#58a6ff', icon: '🔄' },
    engine_started: { color: '#3fb950', icon: '🚀' },
    engine_paused: { color: '#f85149', icon: '⏸' },
    engine_resumed: { color: '#3fb950', icon: '▶' },
  }

  const flashBg = (lastUpdate && (new Date() - lastUpdate) < 3000)
    ? 'rgba(63,185,80,0.15)' : 'transparent'

  return (
    <div style={{
      minHeight: '100vh', background: '#0d1117', color: '#c9d1d9',
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      padding: '20px 16px'
    }}>
      <div style={{ maxWidth: 1100, margin: '0 auto' }}>

        {/* ═══ HEADER ═══ */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          marginBottom: 20, padding: '12px 16px', background: '#161b22',
          border: '1px solid #30363d', borderRadius: 12, transition: 'background 0.3s',
          background: flashBg !== 'transparent' ? flashBg : '#161b22'
        }}>
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 700, color: '#fff', margin: 0 }}>📊 Equity Engine</h1>
            <div style={{ display: 'flex', alignItems: 'center', marginTop: 4, gap: 8 }}>
              <div style={{
                width: 10, height: 10, borderRadius: '50%',
                background: state ? '#3fb950' : '#f85149',
                animation: state ? 'pulse 2s infinite' : 'none'
              }} />
              <span style={{ fontSize: 11, color: '#8b949e' }}>
                {state ? 'Live' : 'Offline'} · {lastUpdate ? timeAgo(lastUpdate.toISOString()) : '—'}
              </span>
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 11, color: '#8b949e' }}>{rs.icon} {rs.label}</div>
            <div style={{ fontSize: 12, color: '#8b949e', marginTop: 2 }}>
              {layer2Features} M15 evals · {totalTrades} trades
            </div>
          </div>
        </div>

        {/* ═══ REGIME BANNER + ENGINE STATUS ═══ */}
        <div style={{
          marginBottom: 20, borderRadius: 12, border: rs.border, background: rs.bg,
          padding: '10px 18px', display: 'flex', alignItems: 'center', gap: 12
        }}>
          <div style={{ width: 8, height: 8, borderRadius: '50%', background: rs.dot }} />
          <span style={{ fontSize: 14, fontWeight: 600, color: rs.text }}>{rs.label}</span>
          <span style={{ fontSize: 12, color: '#8b949e' }}>{regime}</span>
          <span style={{ fontSize: 11, color: '#8b949e', marginLeft: 4 }}>
            {regime === 'risk_on' ? '(allow entries)' :
             regime === 'choppy' ? '(tighten stops)' :
             regime === 'risk_off' ? '(no new entries)' :
             regime === 'crisis' ? '(exit all)' : ''}
          </span>
          {paused && (
            <span style={{ marginLeft: 'auto', fontSize: 12, padding: '4px 12px', borderRadius: 20, background: 'rgba(248,81,73,0.15)', color: '#f85149', fontWeight: 600 }}>
              ⏸ PAUSED {pauseReason ? `— ${pauseReason}` : ''}
            </span>
          )}
          {!paused && (
            <span style={{ marginLeft: 'auto', fontSize: 11, padding: '3px 10px', borderRadius: 20, background: 'rgba(63,185,80,0.12)', color: '#3fb950', fontWeight: 600 }}>
              ▶ RUNNING
            </span>
          )}
        </div>

        {/* ═══ KPI CARDS ═══ */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12, marginBottom: 20 }}>
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 12, padding: 16 }}>
            <div style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Portfolio Equity</div>
            <div style={{ fontSize: 24, fontWeight: 700 }}>{formatMoney(equity)}</div>
            <div style={{ fontSize: 11, color: dailyPnl >= 0 ? '#3fb950' : '#f85149', marginTop: 4 }}>
              {formatMoney(dailyPnl)} ({formatPct(dailyPnlPct)})
            </div>
          </div>
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 12, padding: 16 }}>
            <div style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Active Positions</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: '#58a6ff' }}>{posCount}<span style={{ fontSize: 14, color: '#8b949e' }}>/5</span></div>
            <div style={{ fontSize: 10, color: '#8b949e', marginTop: 4 }}>{posKeys.slice(0, 3).join(', ') || '—'}</div>
          </div>
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 12, padding: 16 }}>
            <div style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Signals Today</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: '#d2a8ff' }}>{layer2Signals}</div>
            <div style={{ fontSize: 10, color: '#8b949e', marginTop: 4 }}>{layer1Approved} L1 approved</div>
          </div>
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 12, padding: 16 }}>
            <div style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Exits Today</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: '#f85149' }}>{layer3Exits}</div>
            <div style={{ fontSize: 10, color: '#8b949e', marginTop: 4 }}>L3 trailing stops</div>
          </div>
        </div>

        {/* ═══ 3-LAYER PIPELINE ═══ */}
        <div style={{
          marginBottom: 20, background: '#161b22', border: '1px solid #30363d',
          borderRadius: 12, padding: '16px 18px'
        }}>
          <div style={{ fontSize: 11, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 }}>
            🔀 Decision Pipeline
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 0, flexWrap: 'wrap' }}>
            {/* Universe */}
            <div style={{ flex: '0 0 auto', textAlign: 'center', padding: '8px 14px', borderRadius: 8, background: 'rgba(139,148,158,0.08)', minWidth: 60 }}>
              <div style={{ fontSize: 9, color: '#8b949e', textTransform: 'uppercase', marginBottom: 2 }}>Universe</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: '#8b949e' }}>{universeSize}</div>
            </div>
            <div style={{ color: '#30363d', fontSize: 18, margin: '0 4px' }}>→</div>
            {/* L1 Approved — click to show all approved stocks */}
            <div style={{ flex: '0 0 auto', textAlign: 'center', padding: '8px 14px', borderRadius: 8, background: 'rgba(88,166,255,0.08)', minWidth: 70, cursor: 'pointer' }}
              onClick={() => {
                setShortlistOpen(true)
                setActiveTab('shortlist')
                setShortlistFilter('approved')
                if (shortlist.length === 0) fetchShortlist()
              }}>
              <div style={{ fontSize: 9, color: '#58a6ff', textTransform: 'uppercase', marginBottom: 2 }}>L1 Approved</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: '#58a6ff' }}>{layer1Approved}</div>
              <div style={{ fontSize: 9, color: '#8b949e', marginTop: 1 }}>SMA200 ↑</div>
            </div>
            <div style={{ color: '#30363d', fontSize: 18, margin: '0 4px' }}>→</div>
            {/* L2 Shortlisted */}
            <div style={{ flex: '0 0 auto', textAlign: 'center', padding: '8px 14px', borderRadius: 8, background: 'rgba(210,168,255,0.1)', minWidth: 70, cursor: 'pointer' }}
              onClick={() => { setShortlistOpen(!shortlistOpen); if (!shortlistOpen) { setActiveTab('shortlist'); fetchShortlist() } }}>
              <div style={{ fontSize: 9, color: '#d2a8ff', textTransform: 'uppercase', marginBottom: 2 }}>L2 Ready</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: '#d2a8ff' }}>{readyCount}</div>
              <div style={{ fontSize: 9, color: '#8b949e', marginTop: 1 }}>XGBoost ✓</div>
            </div>
            <div style={{ color: '#30363d', fontSize: 18, margin: '0 4px' }}>→</div>
            {/* L3 Active */}
            <div style={{ flex: '0 0 auto', textAlign: 'center', padding: '8px 14px', borderRadius: 8, background: 'rgba(63,185,80,0.08)', minWidth: 60 }}>
              <div style={{ fontSize: 9, color: '#3fb950', textTransform: 'uppercase', marginBottom: 2 }}>L3 Active</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: '#3fb950' }}>{posCount}</div>
              <div style={{ fontSize: 9, color: '#8b949e', marginTop: 1 }}>positions</div>
            </div>
            <div style={{ color: '#30363d', fontSize: 18, margin: '0 4px' }}>→</div>
            {/* Exits */}
            <div style={{ flex: '0 0 auto', textAlign: 'center', padding: '8px 14px', borderRadius: 8, background: 'rgba(248,81,73,0.06)', minWidth: 60 }}>
              <div style={{ fontSize: 9, color: '#f85149', textTransform: 'uppercase', marginBottom: 2 }}>Exits</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: '#f85149' }}>{layer3Exits}</div>
              <div style={{ fontSize: 9, color: '#8b949e', marginTop: 1 }}>today</div>
            </div>
          </div>
          {/* Risk bar beneath pipeline */}
          <div style={{ marginTop: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#8b949e', marginBottom: 3 }}>
              <span>Risk Budget</span>
              <span>{posCount}/5</span>
            </div>
            <div style={{ height: 4, background: '#30363d', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{
                height: '100%', borderRadius: 2,
                width: Math.min(100, (posCount / 5) * 100) + '%',
                background: posCount >= 5 ? '#f85149' : posCount >= 4 ? '#d2991d' : '#3fb950',
                transition: 'width 0.5s'
              }} />
            </div>
          </div>
        </div>

        {/* ═══ EQUITY SPARKLINE ═══ */}
        {equityHistory.length > 1 && (
          <div style={{ marginBottom: 20, background: '#161b22', border: '1px solid #30363d', borderRadius: 12, padding: '12px 18px' }}>
            <div style={{ fontSize: 11, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>
              📈 Equity Curve <span style={{ fontSize: 10, color: '#484f58' }}>(last {equityHistory.length} snapshots)</span>
            </div>
            <EquitySparkline data={equityHistory} />
          </div>
        )}

        {/* ═══ TABBED CONTENT: Activity Feed | Shortlist | Positions ═══ */}
        <div style={{ marginBottom: 20 }}>
          {/* Tab bar */}
          <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid #30363d', marginBottom: 14 }}>
            {[
              { id: 'activity', label: '📡 Live Feed', badge: activities.length },
              { id: 'shortlist', label: '🔍 Shortlist', badge: readyCount },
              { id: 'positions', label: '📈 Positions', badge: posCount },
            ].map(tab => (
              <div key={tab.id}
                onClick={() => {
                  setActiveTab(tab.id)
                  if (tab.id === 'shortlist' && shortlist.length === 0) { fetchShortlist() }
                }}
                style={{
                  padding: '8px 16px', cursor: 'pointer', fontSize: 12, fontWeight: 600,
                  borderBottom: activeTab === tab.id ? '2px solid #58a6ff' : '2px solid transparent',
                  color: activeTab === tab.id ? '#fff' : '#8b949e',
                  transition: 'all 0.2s', display: 'flex', alignItems: 'center', gap: 6
                }}>
                {tab.label}
                <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 10, background: '#30363d', color: '#8b949e' }}>
                  {tab.badge}
                </span>
              </div>
            ))}
          </div>

          {/* ── Tab: Live Activity Feed ── */}
          {activeTab === 'activity' && (
            <div style={{
              background: '#161b22', border: '1px solid #30363d', borderRadius: 12,
              maxHeight: 420, overflowY: 'auto', padding: 10,
              fontFamily: "'SF Mono', Monaco, Consolas, monospace", fontSize: 11, lineHeight: '1.7'
            }}>
              {activities.length === 0 && (
                <div style={{ color: '#8b949e', padding: 20, textAlign: 'center' }}>
                  Waiting for engine events...
                </div>
              )}
              {activities.map((act, i) => {
                const es = eventTypeStyles[act.event_type] || { color: '#8b949e', icon: '●' }
                const ts = (act.created_at || '').slice(11, 19)
                try {
                  const detail = typeof act.detail_json === 'string' ? JSON.parse(act.detail_json) : (act.detail_json || {})
                  let detailStr = ''
                  if (detail.prob != null) detailStr += ` prob=${(detail.prob * 100).toFixed(1)}%`
                  if (detail.price != null) detailStr += ` @$${detail.price}`
                  if (detail.reason) detailStr += ` [${detail.reason}]`
                  if (detail.qty) detailStr += ` ${detail.qty}sh`
                  return (
                    <div key={act.id || i} style={{
                      padding: '3px 4px', borderBottom: '1px solid rgba(48,54,61,0.3)',
                      display: 'flex', gap: 6, alignItems: 'baseline'
                    }}>
                      <span style={{ color: '#484f58', flexShrink: 0, minWidth: 60 }}>{ts}</span>
                      <span style={{ color: es.color, flexShrink: 0 }}>{es.icon}</span>
                      <span style={{ color: es.color === '#d2a8ff' ? '#d2a8ff' : '#c9d1d9' }}>
                        {act.symbol ? <span style={{ fontWeight: 600, marginRight: 4 }}>{act.symbol}</span> : null}
                        {act.message}
                      </span>
                      {detailStr ? <span style={{ color: '#8b949e', fontSize: 10 }}>{detailStr}</span> : null}
                    </div>
                  )
                } catch {
                  return (
                    <div key={act.id || i} style={{
                      padding: '3px 4px', borderBottom: '1px solid rgba(48,54,61,0.3)',
                      display: 'flex', gap: 6, alignItems: 'baseline'
                    }}>
                      <span style={{ color: '#484f58', flexShrink: 0, minWidth: 60 }}>{ts}</span>
                      <span style={{ color: es.color }}>{es.icon}</span>
                      <span>{act.message}</span>
                    </div>
                  )
                }
              })}
              <div ref={activityEndRef} />
            </div>
          )}

          {/* ── Tab: Shortlist Drill-Down ── */}
          {activeTab === 'shortlist' && (
            <div style={{
              background: '#161b22', border: '1px solid #30363d', borderRadius: 12,
              overflow: 'hidden'
            }}>
              {/* Summary bar + filter tabs */}
              <div style={{ padding: '8px 16px', display: 'flex', gap: 8, alignItems: 'center', borderBottom: '1px solid #30363d', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 10, color: '#8b949e' }}>
                  {readyCount} ready · {enteredCount} entered · {shortlist.length - readyCount - enteredCount - blockedCount} evaluating
                </span>
                <div style={{ display: 'flex', gap: 4, marginLeft: 8 }}>
                  {[
                    { id: 'all', label: 'All', count: shortlist.length },
                    { id: 'approved', label: 'L1 ✓', count: shortlist.filter(r => r.approved).length },
                    { id: 'ready', label: 'L2 Ready', count: readyCount },
                    { id: 'entered', label: 'Active', count: enteredCount },
                  ].map(f => (
                    <div key={f.id} onClick={() => setShortlistFilter(f.id)} style={{
                      fontSize: 9, padding: '2px 8px', borderRadius: 10, cursor: 'pointer',
                      background: shortlistFilter === f.id ? 'rgba(88,166,255,0.2)' : '#21262d',
                      color: shortlistFilter === f.id ? '#58a6ff' : '#8b949e',
                      border: shortlistFilter === f.id ? '1px solid rgba(88,166,255,0.3)' : '1px solid transparent',
                      transition: 'all 0.15s',
                    }}>
                      {f.label} {f.count}
                    </div>
                  ))}
                </div>
                <button onClick={fetchShortlist} disabled={shortlistLoading}
                  style={{
                    marginLeft: 'auto', fontSize: 10, padding: '4px 10px', borderRadius: 6,
                    border: '1px solid #30363d', background: '#21262d', color: '#c9d1d9',
                    cursor: 'pointer'
                  }}>
                  {shortlistLoading ? '⏳ Loading...' : '🔄 Refresh'}
                </button>
              </div>

              {shortlistLoading && shortlist.length === 0 && (
                <div style={{ padding: 30, textAlign: 'center', color: '#8b949e' }}>
                  Loading shortlist data from engine...
                </div>
              )}

              {!shortlistLoading && shortlist.length === 0 && (
                <div style={{ padding: 30, textAlign: 'center', color: '#8b949e' }}>
                  No shortlist data yet. Engine needs to run and publish shortlist snapshots (every 15 min).
                </div>
              )}

              {(() => {
                // Apply filter
                const filtered = shortlist.filter(r => {
                  if (shortlistFilter === 'approved') return r.approved === true
                  if (shortlistFilter === 'ready') return r.status === 'ready'
                  if (shortlistFilter === 'entered') return r.status === 'entered'
                  return true // 'all'
                })
                if (filtered.length === 0) {
                  return <div style={{ padding: 30, textAlign: 'center', color: '#8b949e' }}>No stocks match this filter.</div>
                }
                return (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr style={{ color: '#8b949e', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                        <th style={{ textAlign: 'left', padding: '8px 12px', borderBottom: '1px solid #30363d' }}>Symbol</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>Price</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>Prob</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>Prob vs 65%</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>RSI</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>ATR%</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>VWAP%</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>Vol Z</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>SMA200%</th>
                        <th style={{ textAlign: 'center', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shortlist.map(row => {
                        const prob = row.xgb_prob
                        const probColor = prob >= 0.70 ? '#3fb950' : prob >= 0.65 ? '#d2991d' : '#8b949e'
                        const statusColor = row.status === 'ready' ? '#3fb950' : row.status === 'entered' ? '#58a6ff' : row.status === 'blocked' ? '#f85149' : '#8b949e'
                        const isExpanded = expandedSymbol === row.symbol
                        const snaps = row.recent_snapshots
                          ? (typeof row.recent_snapshots === 'string' ? JSON.parse(row.recent_snapshots) : row.recent_snapshots)
                          : []
                        return (
                          <tbody key={row.symbol}>
                            <tr onClick={() => setExpandedSymbol(isExpanded ? null : row.symbol)}
                              style={{
                                borderBottom: '1px solid rgba(48,54,61,0.4)', cursor: 'pointer',
                                fontFamily: "'SF Mono', Monaco, monospace", fontSize: 11,
                                background: isExpanded ? 'rgba(88,166,255,0.05)' : 'transparent',
                                transition: 'background 0.15s'
                              }}>
                              <td style={{ padding: '9px 12px', fontWeight: 600 }}>
                                <span style={{ marginRight: 4 }}>{isExpanded ? '▼' : '▶'}</span>
                                {row.symbol}
                              </td>
                              <td style={{ padding: '9px 10px', textAlign: 'right' }}>{row.current_price != null ? '$' + row.current_price.toFixed(2) : '—'}</td>
                              <td style={{ padding: '9px 10px', textAlign: 'right', color: probColor, fontWeight: 600 }}>
                                {prob != null ? (prob * 100).toFixed(1) + '%' : '—'}
                              </td>
                              <td style={{ padding: '9px 10px', textAlign: 'right', color: (row.prob_vs_threshold || 0) >= 0 ? '#3fb950' : '#f85149' }}>
                                {row.prob_vs_threshold != null ? formatPctV(row.prob_vs_threshold) : '—'}
                              </td>
                              <td style={{ padding: '9px 10px', textAlign: 'right', color: row.rsi_14 > 70 ? '#f85149' : row.rsi_14 < 30 ? '#3fb950' : '#c9d1d9' }}>
                                {row.rsi_14 != null ? row.rsi_14.toFixed(1) : '—'}
                              </td>
                              <td style={{ padding: '9px 10px', textAlign: 'right' }}>{row.atr_pct != null ? (row.atr_pct * 100).toFixed(2) + '%' : '—'}</td>
                              <td style={{ padding: '9px 10px', textAlign: 'right' }}>{row.vwap_distance_pct != null ? (row.vwap_distance_pct * 100).toFixed(2) + '%' : '—'}</td>
                              <td style={{ padding: '9px 10px', textAlign: 'right' }}>
                                {row.volume_zscore != null ? (row.volume_zscore > 0 ? '+' : '') + row.volume_zscore.toFixed(2) : '—'}
                              </td>
                              <td style={{ padding: '9px 10px', textAlign: 'right', color: (row.price_vs_sma200_pct || 0) >= 0 ? '#3fb950' : '#f85149' }}>
                                {row.price_vs_sma200_pct != null ? formatPctV(row.price_vs_sma200_pct) : '—'}
                              </td>
                              <td style={{ padding: '9px 10px', textAlign: 'center' }}>
                                <span style={{
                                  fontSize: 10, padding: '2px 8px', borderRadius: 10,
                                  color: statusColor, background: statusColor === '#3fb950' ? 'rgba(63,185,80,0.12)' :
                                    statusColor === '#f85149' ? 'rgba(248,81,73,0.12)' :
                                    statusColor === '#d2991d' ? 'rgba(210,153,29,0.12)' :
                                    'rgba(88,166,255,0.10)',
                                  fontWeight: 600, textTransform: 'uppercase'
                                }}>
                                  {row.status || '—'}
                                </span>
                                {row.block_reason && (
                                  <div style={{ fontSize: 9, color: '#8b949e', marginTop: 2 }}>{row.block_reason}</div>
                                )}
                              </td>
                            </tr>
                            {/* Symbol Monitor Panel (expandable) */}
                            {isExpanded && (
                              <tr>
                                <td colSpan={10} style={{ padding: '12px 18px', background: 'rgba(13,17,23,0.6)' }}>
                                  <div style={{ marginBottom: 8, fontSize: 12, fontWeight: 600, color: '#fff' }}>
                                    {row.symbol} — Monitor
                                  </div>
                                  {/* Gauges */}
                                  <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
                                    <Gauge value={prob != null ? prob * 100 : 0} max={100} color={probColor} label="XGBoost Prob"
                                      formatVal={v => v.toFixed(1) + '%'} />
                                    <Gauge value={row.rsi_14 || 50} max={100} color={row.rsi_14 > 70 ? '#f85149' : row.rsi_14 < 30 ? '#3fb950' : '#58a6ff'} label="RSI(14)"
                                      formatVal={v => v.toFixed(1)} />
                                    <Gauge value={row.price_vs_sma200_pct != null ? Math.max(0, row.price_vs_sma200_pct + 10) : 10} max={20}
                                      color={(row.price_vs_sma200_pct || 0) >= 0 ? '#3fb950' : '#f85149'}
                                      label="vs SMA200" formatVal={v => (row.price_vs_sma200_pct != null ? formatPctV(row.price_vs_sma200_pct) : '—')} />
                                    <Gauge value={row.atr_pct != null ? row.atr_pct * 500 : 0} max={10} color="#8b949e" label="ATR%"
                                      formatVal={v => (row.atr_pct != null ? (row.atr_pct * 100).toFixed(2) + '%' : '—')} />
                                  </div>
                                  {/* Overlay chart: Price + XGBoost Probability */}
                                  <SymbolMonitorChart snapshots={snaps} symbol={row.symbol} />
                                </td>
                              </tr>
                            )}
                          </tbody>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
            )})()}
            </div>
          )}

          {/* ── Tab: Positions ── */}
          {activeTab === 'positions' && (
            <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 12, overflow: 'hidden' }}>
              {posKeys.length === 0 ? (
                <div style={{ padding: 40, textAlign: 'center', color: '#8b949e' }}>No active positions</div>
              ) : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr style={{ color: '#8b949e', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                        <th style={{ textAlign: 'left', padding: '8px 12px', borderBottom: '1px solid #30363d' }}>Symbol</th>
                        <th style={{ textAlign: 'left', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>Side</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>Entry</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>Stop</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>Trail</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>Qty</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>Bars</th>
                        <th style={{ textAlign: 'left', padding: '8px 10px', borderBottom: '1px solid #30363d' }}>Stop Risk</th>
                      </tr>
                    </thead>
                    <tbody>
                      {posKeys.map(sym => {
                        const pos = positions[sym]
                        const entry = pos.entry_price || 0
                        const trail = pos.trailing_stop || pos.stop_loss || 0
                        const stopDist = entry > 0 ? Math.max(0, Math.min(100, ((entry - trail) / entry) * 300)) : 0
                        const riskColor = stopDist > 60 ? '#f85149' : stopDist > 30 ? '#d2991d' : '#3fb950'
                        return (
                          <tr key={sym} style={{ borderBottom: '1px solid rgba(48,54,61,0.4)', fontSize: 11, fontFamily: "'SF Mono', Monaco, monospace" }}>
                            <td style={{ padding: '10px 12px', fontWeight: 600 }}>{sym}</td>
                            <td style={{ padding: '10px 10px', color: pos.side === 'LONG' ? '#3fb950' : '#f85149' }}>{pos.side || 'LONG'}</td>
                            <td style={{ padding: '10px 10px', textAlign: 'right' }}>{formatMoney(pos.entry_price)}</td>
                            <td style={{ padding: '10px 10px', textAlign: 'right', color: '#f85149' }}>{formatMoney(pos.stop_loss)}</td>
                            <td style={{ padding: '10px 10px', textAlign: 'right', color: '#d2991d' }}>{formatMoney(pos.trailing_stop)}</td>
                            <td style={{ padding: '10px 10px', textAlign: 'right' }}>{pos.quantity || 0}</td>
                            <td style={{ padding: '10px 10px', textAlign: 'right', color: '#8b949e' }}>{pos.bars_held || 0}</td>
                            <td style={{ padding: '10px 10px' }}>
                              <div style={{ width: 80, height: 5, background: '#30363d', borderRadius: 3, overflow: 'hidden' }}>
                                <div style={{
                                  height: '100%', borderRadius: 3, background: riskColor,
                                  width: stopDist + '%', transition: 'width 0.5s'
                                }} />
                              </div>
                              <div style={{ fontSize: 9, color: '#8b949e', marginTop: 2 }}>
                                {entry > 0 ? ((entry - trail) / entry * 100).toFixed(2) + '%' : '—'}
                              </div>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ═══ END ═══ */}
      </div>

      <style jsx>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        @keyframes flash {
          0% { background-color: rgba(63,185,80,0.15); }
          100% { background-color: transparent; }
        }
      `}</style>
    </div>
  )
}