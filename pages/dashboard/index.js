import { useEffect, useState } from 'react'
import supabase from '../../lib/supabase'

const BUCKET_LABELS = { base_yield: 'Base Yield', alpha: 'Alpha', convexity: 'Convexity' }
const BUCKET_COLORS = { base_yield: 'text-blue-400', alpha: 'text-green-400', convexity: 'text-purple-400' }
const DIR_COLORS = { LONG: 'bg-green-900/40 text-green-400', SHORT: 'bg-red-900/40 text-red-400', NEUTRAL: 'bg-yellow-900/40 text-yellow-400' }

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

export default function Dashboard() {
  const [connected, setConnected] = useState(false)
  const [signals, setSignals] = useState([])
  const [loading, setLoading] = useState(true)
  const [bucketCounts, setBucketCounts] = useState({})
  const [lastUpdate, setLastUpdate] = useState(null)

  useEffect(() => {
    if (!supabase) {
      setConnected(false)
      setLoading(false)
      return
    }
    setConnected(true)

    // Fetch existing signals
    supabase
      .from('signals')
      .select('*')
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

    // Subscribe to new signals in realtime
    const channel = supabase
      .channel('signals')
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'signals' },
        (payload) => {
          setSignals(prev => [payload.new, ...prev])
          setLastUpdate(new Date())
        }
      )
      .subscribe()

    return () => { supabase.removeChannel(channel) }
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
        <div className="flex items-center justify-between mb-8">
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
            <span className="text-xs text-market-400">Live</span>
            {lastUpdate && <TimeAgo ts={lastUpdate} />}
          </div>
        </div>

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

        {/* Signal list */}
        {signals.length > 0 && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-market-300 uppercase tracking-wider">
                Signal Feed ({signals.length})
              </h2>
            </div>
            <div className="space-y-2">
              {signals.map((s) => (
                <div
                  key={s.id}
                  className="flex items-center justify-between px-4 py-3 rounded-xl border border-market-800/30 bg-market-900/30 hover:bg-market-800/30 transition-colors"
                >
                  <div className="flex items-center gap-4">
                    <span className="text-sm font-mono font-semibold text-white w-16">{s.ticker}</span>
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
                  </div>
                  <div className="flex items-center gap-3">
                    {s.signal_json?.stop_loss && (
                      <span className="text-[10px] text-red-400/60 font-mono">
                        SL {s.signal_json.stop_loss.toFixed(2)}
                      </span>
                    )}
                    <TimeAgo ts={s.created_at} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
