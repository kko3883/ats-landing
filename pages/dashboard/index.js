import { useEffect, useState } from 'react'
import supabase from '../../lib/supabase'

export default function Dashboard() {
  const [connected, setConnected] = useState(false)
  const [data, setData] = useState(null)

  useEffect(() => {
    if (!supabase) {
      setConnected(false)
      return
    }
    setConnected(true)

    // Example: subscribe to signals table
    const channel = supabase
      .channel('signals')
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'signals' },
        (payload) => setData(payload.new)
      )
      .subscribe()

    return () => { supabase.removeChannel(channel) }
  }, [])

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
        <h1 className="text-2xl font-bold text-white mb-6">Dashboard</h1>
        {/* Real dashboard components go here */}
        {data && <pre className="text-xs">{JSON.stringify(data, null, 2)}</pre>}
      </div>
    </div>
  )
}
