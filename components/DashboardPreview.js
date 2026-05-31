export default function DashboardPreview() {
  return (
    <section id="dashboard" className="border-t border-market-800/30">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-24">
        <div className="text-center mb-12">
          <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">Live Dashboard</h2>
          <p className="text-market-400 max-w-xl mx-auto">
            Real-time signal status, portfolio allocation, and execution log — secured behind authentication.
          </p>
        </div>

        <div className="relative mx-auto max-w-3xl">
          {/* Dashboard mockup */}
          <div className="rounded-2xl border border-market-800/50 bg-market-900/50 backdrop-blur-sm overflow-hidden shadow-2xl shadow-black/50">
            {/* Top bar */}
            <div className="flex items-center justify-between px-5 py-3 border-b border-market-800/50">
              <div className="flex items-center gap-3">
                <div className="flex gap-1.5">
                  <div className="w-2.5 h-2.5 rounded-full bg-red-500/50" />
                  <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/50" />
                  <div className="w-2.5 h-2.5 rounded-full bg-green-500/50" />
                </div>
                <span className="text-xs text-market-500 font-mono">ats.coolpaperplane.win/dashboard</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                <span className="text-xs text-market-400">Signal Feed</span>
              </div>
            </div>

            {/* Mock content */}
            <div className="p-6 space-y-4">
              {/* Bucket cards */}
              <div className="grid grid-cols-3 gap-3">
                {['Base Yield', 'Alpha', 'Convexity'].map((b) => (
                  <div key={b} className="rounded-lg border border-market-800/30 p-3">
                    <div className="text-xs text-market-500 mb-1">{b}</div>
                    <div className="text-lg font-semibold text-green-400">+2.14%</div>
                    <div className="text-[10px] text-market-600">MTD return</div>
                  </div>
                ))}
              </div>

              {/* Signal list mock */}
              <div className="space-y-2">
                {['QQQ', 'SPY', 'TLT', 'GLD', 'IWM'].map((ticker, i) => (
                  <div key={ticker} className="flex items-center justify-between px-3 py-2 rounded-lg bg-market-800/30">
                    <div className="flex items-center gap-3">
                      <span className="text-sm font-mono text-white">{ticker}</span>
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        i % 2 === 0 ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'
                      }`}>
                        {i % 2 === 0 ? 'LONG' : 'SHORT'}
                      </span>
                    </div>
                    <span className="text-xs text-market-500 font-mono">
                      VIX: {15 + i * 2}.{1 + i * 3}
                    </span>
                  </div>
                ))}
              </div>

              {/* Bottom row */}
              <div className="flex items-center justify-between text-xs text-market-600 pt-2 border-t border-market-800/30">
                <span>Last updated: Just now</span>
                <span>Session: active</span>
              </div>
            </div>
          </div>

          {/* Auth overlay hint */}
          <div className="mt-8 text-center">
            <p className="text-sm text-market-500 mb-4">
              Dashboard requires authentication. Supabase Auth + RLS keeps your trading data private.
            </p>
            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-market-800/50 text-market-400 text-sm">
              <span>🔒</span>
              <span>Coming with Supabase integration</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
