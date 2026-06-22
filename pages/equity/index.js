import { useEffect, useState } from 'react'
import supabase from '../../lib/supabase'

function formatMoney(v) {
  if (v == null || v === undefined) return '—'
  return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function formatPct(v) {
  if (v == null || v === undefined) return '—'
  const pct = (Number(v) * 100).toFixed(2)
  return (Number(v) >= 0 ? '+' : '') + pct + '%'
}

export default function EquityDashboard() {
  const [state, setState] = useState(null)
  const [connected, setConnected] = useState(false)
  const [lastUpdate, setLastUpdate] = useState(null)

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
          if (error) {
            console.error('Equity state fetch error:', error)
            return
          }
          if (data && data.length > 0) {
            setState(data[0])
            setLastUpdate(new Date())
          }
        })
    }
    fetchState()

    // Subscribe to realtime updates
    const channel = supabase
      .channel('equity_state')
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'equity_state' },
        (payload) => {
          setState(payload.new)
          setLastUpdate(new Date())
        }
      )
      .subscribe()

    // Also poll as a fallback (every 5s if no realtime event)
    const poll = setInterval(fetchState, 5000)

    return () => {
      supabase.removeChannel(channel)
      clearInterval(poll)
    }
  }, [])

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
  const entriesToday = s.entries_today || 0

  // Parse positions
  let positions = {}
  try {
    positions = typeof s.positions_json === 'string' ? JSON.parse(s.positions_json) : (s.positions_json || {})
  } catch { positions = {} }
  const posKeys = Object.keys(positions)

  // Parse trades
  let trades = []
  try {
    trades = typeof s.trades_json === 'string' ? JSON.parse(s.trades_json) : (s.trades_json || [])
  } catch { trades = [] }

  const regimeStyles = {
    risk_on: { border: '1px solid rgba(63,185,80,0.4)', bg: 'rgba(63,185,80,0.1)', text: '#3fb950', dot: '#3fb950', label: 'Risk-On' },
    choppy: { border: '1px solid rgba(210,153,29,0.4)', bg: 'rgba(210,153,29,0.1)', text: '#d2991d', dot: '#d2991d', label: 'Choppy' },
    risk_off: { border: '1px solid rgba(248,81,73,0.4)', bg: 'rgba(248,81,73,0.1)', text: '#f85149', dot: '#f85149', label: 'Risk-Off' },
    crisis: { border: '1px solid rgba(248,81,73,0.5)', bg: 'rgba(248,81,73,0.15)', text: '#f85149', dot: '#f85149', label: 'Crisis' },
  }
  const rs = regimeStyles[regime] || regimeStyles.choppy

  return (
    <div style={{ minHeight: '100vh', background: '#0d1117', color: '#c9d1d9', fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", padding: '20px 16px' }}>
      <div style={{ maxWidth: 960, margin: '0 auto' }}>

        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
          <div>
            <h1 style={{ fontSize: 24, fontWeight: 700, color: '#fff', margin: 0 }}>📊 Equity Engine</h1>
            <div style={{ display: 'flex', alignItems: 'center', marginTop: 4, gap: 8 }}>
              <div style={{
                width: 10, height: 10, borderRadius: '50%',
                background: state ? '#3fb950' : '#f85149',
                animation: state ? 'pulse 2s infinite' : 'none'
              }} />
              <span style={{ fontSize: 11, color: '#8b949e' }}>{state ? 'Live' : 'Offline — no data'}</span>
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 11, color: '#8b949e' }}>Last update</div>
            <div style={{ fontSize: 13 }}>{lastUpdate ? lastUpdate.toLocaleTimeString() : '—'}</div>
          </div>
        </div>

        {/* Regime Banner */}
        <div style={{
          marginBottom: 20, borderRadius: 12, border: rs.border, background: rs.bg, padding: '14px 18px',
          display: 'flex', alignItems: 'center', gap: 12
        }}>
          <div style={{ width: 8, height: 8, borderRadius: '50%', background: rs.dot }} />
          <span style={{ fontSize: 14, fontWeight: 600, color: rs.text }}>{rs.label}</span>
          <span style={{ fontSize: 12, color: '#8b949e' }}>{regime}</span>
          {paused && (
            <span style={{ marginLeft: 'auto', fontSize: 12, padding: '4px 12px', borderRadius: 20, background: 'rgba(248,81,73,0.15)', color: '#f85149', fontWeight: 600 }}>
              ⏸ PAUSED {pauseReason ? `— ${pauseReason}` : ''}
            </span>
          )}
        </div>

        {/* KPI Cards */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 14, marginBottom: 24 }}>
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 12, padding: 18 }}>
            <div style={{ fontSize: 11, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Portfolio Equity</div>
            <div style={{ fontSize: 26, fontWeight: 700 }}>{formatMoney(equity)}</div>
            <div style={{ fontSize: 11, color: dailyPnl >= 0 ? '#3fb950' : '#f85149', marginTop: 4 }}>
              {formatMoney(dailyPnl)} ({formatPct(dailyPnlPct)})
            </div>
          </div>
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 12, padding: 18 }}>
            <div style={{ fontSize: 11, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Active Positions</div>
            <div style={{ fontSize: 26, fontWeight: 700, color: '#58a6ff' }}>{posCount}</div>
            <div style={{ fontSize: 11, color: '#8b949e', marginTop: 4 }}>Max: 5</div>
          </div>
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 12, padding: 18 }}>
            <div style={{ fontSize: 11, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Total Trades</div>
            <div style={{ fontSize: 26, fontWeight: 700, color: '#bc8cff' }}>{totalTrades}</div>
            <div style={{ fontSize: 11, color: '#8b949e', marginTop: 4 }}>Entries today: {entriesToday}</div>
          </div>
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 12, padding: 18 }}>
            <div style={{ fontSize: 11, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Engine Status</div>
            <div style={{ fontSize: 26, fontWeight: 700, color: paused ? '#f85149' : '#3fb950' }}>
              {paused ? '⏸ PAUSED' : '▶ RUNNING'}
            </div>
            <div style={{ fontSize: 11, color: '#8b949e', marginTop: 4 }}>{pauseReason || 'All systems operational'}</div>
          </div>
        </div>

        {/* Layer activity */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 24 }}>
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8, padding: 12, textAlign: 'center' }}>
            <div style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>Layer 1 — Macro</div>
            <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4 }}>{layer1Approved}</div>
            <div style={{ fontSize: 10, color: '#8b949e', marginTop: 2 }}>stocks approved (SMA200)</div>
          </div>
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8, padding: 12, textAlign: 'center' }}>
            <div style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>Layer 2 — Tactical</div>
            <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4 }}>{layer2Signals}</div>
            <div style={{ fontSize: 10, color: '#8b949e', marginTop: 2 }}>XGBoost signals ({layer2Features} evals)</div>
          </div>
          <div style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8, padding: 12, textAlign: 'center' }}>
            <div style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>Layer 3 — Micro</div>
            <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4 }}>{layer3Exits}</div>
            <div style={{ fontSize: 10, color: '#8b949e', marginTop: 2 }}>trailing stop exits</div>
          </div>
        </div>

        {/* Positions table */}
        <div style={{ marginBottom: 24 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 }}>📈 Active Positions</div>
          {posKeys.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 30, color: '#8b949e' }}>No active positions</div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ color: '#8b949e', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    <th style={{ textAlign: 'left', padding: '8px 12px', borderBottom: '1px solid #30363d' }}>Symbol</th>
                    <th style={{ textAlign: 'left', padding: '8px 12px', borderBottom: '1px solid #30363d' }}>Side</th>
                    <th style={{ textAlign: 'right', padding: '8px 12px', borderBottom: '1px solid #30363d' }}>Entry</th>
                    <th style={{ textAlign: 'right', padding: '8px 12px', borderBottom: '1px solid #30363d' }}>Stop</th>
                    <th style={{ textAlign: 'right', padding: '8px 12px', borderBottom: '1px solid #30363d' }}>Trail</th>
                    <th style={{ textAlign: 'right', padding: '8px 12px', borderBottom: '1px solid #30363d' }}>Qty</th>
                    <th style={{ textAlign: 'right', padding: '8px 12px', borderBottom: '1px solid #30363d' }}>Bars</th>
                  </tr>
                </thead>
                <tbody>
                  {posKeys.map(sym => {
                    const pos = positions[sym]
                    return (
                      <tr key={sym} style={{ borderBottom: '1px solid rgba(48,54,61,0.5)', fontSize: 12, fontFamily: "'SF Mono', Monaco, monospace" }}>
                        <td style={{ padding: '10px 12px', fontWeight: 600 }}>{sym}</td>
                        <td style={{ padding: '10px 12px', color: pos.side === 'LONG' ? '#3fb950' : '#f85149' }}>{pos.side || 'LONG'}</td>
                        <td style={{ padding: '10px 12px', textAlign: 'right' }}>{formatMoney(pos.entry_price)}</td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', color: '#f85149' }}>{formatMoney(pos.stop_loss)}</td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', color: '#d2991d' }}>{formatMoney(pos.trailing_stop)}</td>
                        <td style={{ padding: '10px 12px', textAlign: 'right' }}>{pos.quantity || 0}</td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', color: '#8b949e' }}>{pos.bars_held || 0}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Trade log */}
        <div style={{ marginBottom: 24 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 }}>📋 Recent Trades</div>
          {trades.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 30, color: '#8b949e' }}>No trades recorded yet</div>
          ) : (
            <div>
              {trades.slice(-15).map((t, i) => {
                const ts = (t.ts || '').slice(0, 19).replace('T', ' ')
                const sym = t.symbol || ''
                const event = t.event || ''
                const pnl = t.pnl_dollar != null ? formatMoney(t.pnl_dollar) : ''
                const reason = t.exit_reason || t.reason || ''
                const pnlColor = t.pnl_dollar != null ? (t.pnl_dollar >= 0 ? '#3fb950' : '#f85149') : ''
                return (
                  <div key={i} style={{ fontSize: 11, padding: '4px 0', borderBottom: '1px solid rgba(48,54,61,0.3)' }}>
                    <span style={{ color: '#8b949e', marginRight: 8 }}>{ts}</span>
                    <span style={{ fontWeight: 600 }}>{sym}</span>
                    <span style={{ marginLeft: 8 }}>{event}</span>
                    {pnl && <span style={{ marginLeft: 8, color: pnlColor }}>{pnl}</span>}
                    {reason && <span style={{ marginLeft: 8, color: '#8b949e' }}>[{reason}]</span>}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Risk bar */}
        <div>
          <div style={{ fontSize: 11, color: '#8b949e', marginBottom: 4 }}>Risk Budget ({posCount}/5 positions)</div>
          <div style={{ height: 6, background: '#30363d', borderRadius: 3, overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 3,
              width: Math.min(100, (posCount / 5) * 100) + '%',
              background: posCount >= 5 ? '#f85149' : posCount >= 4 ? '#d2991d' : '#3fb950',
              transition: 'width 0.5s'
            }} />
          </div>
        </div>

      </div>

      <style jsx>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  )
}