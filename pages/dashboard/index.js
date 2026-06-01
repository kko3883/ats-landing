import { useEffect, useState } from 'react'
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

function signalType(signal) {
  const dir = (signal.direction || '').toLowerCase()
  return dir === 'long' ? 'LONG' : dir === 'short' ? 'SHORT' : 'NEUTRAL'
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

// ── Card Components ─────────────────────────────────────────────────────

function SignalCard({ signal }) {
  const bucket = signal.bucket || 'alpha'
  const style = BUCKET_COLORS[bucket] || BUCKET_COLORS.alpha
  const meta = signal.signal_json || {}
  const ctx = meta.context || {}

  return (
    <div className={`${style.bg} ${style.border} border rounded-lg p-4`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-lg font-bold text-white">{signal.ticker}</span>
        <span className={`text-xs font-mono px-2 py-0.5 rounded-full ${signal.direction === 'LONG' ? 'bg-emerald-800 text-emerald-200' : 'bg-red-800 text-red-200'}`}>
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
        {meta.strategy_name && (
          <div className="flex justify-between">
            <span>Strategy</span>
            <span className="text-market-400">{meta.strategy_name.replace(/_/g, ' ')}</span>
          </div>
        )}
        {ctx.breakout && <span className="inline-block text-xs bg-blue-800 text-blue-200 px-2 py-0.5 rounded mt-1">Breakout</span>}
      </div>
    </div>
  )
}

function HkCard({ symbol, candidate_type, rs_zscore, beta_group }) {
  const isLong = candidate_type === 'long'
  return (
    <div className={`${isLong ? 'bg-emerald-900/20 border-emerald-700/40' : 'bg-red-900/20 border-red-700/40'} border rounded-lg p-3`}>
      <div className="flex items-center justify-between mb-1">
        <span className="font-bold text-white">{symbol}</span>
        <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${isLong ? 'bg-emerald-800 text-emerald-200' : 'bg-red-800 text-red-200'}`}>
          {isLong ? 'LONG' : 'SHORT'}
        </span>
      </div>
      <div className="text-xs text-market-400 space-y-0.5">
        <div className="flex justify-between">
          <span>RS Z-Score</span>
          <span className={isLong ? 'text-emerald-300' : 'text-red-300'}>{rs_zscore > 0 ? '+' : ''}{rs_zscore.toFixed(3)}</span>
        </div>
        <div className="flex justify-between">
          <span>Beta Group</span>
          <span className="text-market-300">{beta_group?.replace('group_', '')}</span>
        </div>
      </div>
    </div>
  )
}

function BucketSection({ title, signals, style }) {
  if (!signals || signals.length === 0) return null
  return (
    <div className="mb-6">
      <h3 className={`text-sm font-semibold uppercase tracking-wider mb-3 ${style.text}`}>{title} ({signals.length})</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {signals.map(s => <SignalCard key={s.id} signal={s} />)}
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
  const [tab, setTab] = useState('all') // 'all' | 'us' | 'hk'

  useEffect(() => {
    if (!supabase) {
      setConnected(false)
      setLoading(false)
      return
    }

    setConnected(true)

    // Fetch latest signals
    supabase
      .from('signals')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(50)
      .then(({ data, error }) => {
        if (!error && data) setSignals(data)
      })

    // Fetch latest HK watchlist (most recent batch by generated_at)
    supabase
      .from('watchlist_hk')
      .select('*')
      .order('id', { ascending: false })
      .limit(50)
      .then(({ data, error }) => {
        if (!error && data) {
          // Keep only the most recent batch
          const latestBatch = data[0]?.generated_at
          setHkWatchlist(latestBatch ? data.filter(r => r.generated_at === latestBatch) : data)
        }
        setLoading(false)
      })

    // Real-time: listen for new signals
    const channel = supabase
      .channel('signals')
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'signals' },
        (payload) => setSignals(prev => [payload.new, ...prev])
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

  // ── Group signals by VIX zone ──
  const byVixZone = groupBy(signals, s => s.vix_zone || 'unknown')
  const zoneOrder = ['high_beta', 'moderate_beta', 'low_beta', 'defensive']

  // Separate HK long vs short
  const hkLong = hkWatchlist.filter(r => r.candidate_type === 'long').sort((a, b) => b.rs_zscore - a.rs_zscore)
  const hkShort = hkWatchlist.filter(r => r.candidate_type === 'short').sort((a, b) => a.rs_zscore - b.rs_zscore)

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
            {/* US Signals */}
            <div className="mb-2">
              <h2 className="text-lg font-semibold text-white">US Signals</h2>
              <p className="text-xs text-market-500 mb-4">From signal engine · Auto-refreshes in real-time</p>
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
                />
              ))
            )}

            {/* Unknown zone signals */}
            {byVixZone['unknown'] && byVixZone['unknown'].length > 0 && (
              <BucketSection
                title="Other"
                signals={byVixZone['unknown']}
                style={BUCKET_COLORS.convexity}
              />
            )}
          </div>
        )}

        {!loading && tab !== 'us' && (
          <div>
            {/* HK Watchlist Section */}
            <div className="mb-2">
              <h2 className="text-lg font-semibold text-white">HK Watchlist</h2>
              <p className="text-xs text-market-500 mb-4">
                From Longbridge market data · Updated daily after market close
              </p>
            </div>

            {hkWatchlist.length === 0 ? (
              <div className="text-center py-12 border border-dashed border-market-700 rounded-lg">
                <p className="text-market-500">HK watchlist will appear here after the daily screening runs.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Long candidates */}
                <div>
                  <h3 className="text-sm font-semibold uppercase tracking-wider text-emerald-400 mb-3">
                    Top Long Candidates ({hkLong.length})
                  </h3>
                  <div className="space-y-2">
                    {hkLong.map((r, i) => (
                      <HkCard key={`${r.id}-${i}`} {...r} />
                    ))}
                  </div>
                </div>

                {/* Short candidates */}
                <div>
                  <h3 className="text-sm font-semibold uppercase tracking-wider text-red-400 mb-3">
                    Top Short Candidates ({hkShort.length})
                  </h3>
                  <div className="space-y-2">
                    {hkShort.map((r, i) => (
                      <HkCard key={`${r.id}-${i}`} {...r} />
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
