export default function Hero() {
  return (
    <section className="relative overflow-hidden">
      {/* Background glow */}
      <div className="absolute inset-0 bg-gradient-to-b from-green-900/20 via-transparent to-market-950 pointer-events-none" />
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 w-[800px] h-[400px] bg-green-600/10 rounded-full blur-[120px] pointer-events-none" />

      <div className="relative max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 pt-24 pb-32 text-center">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-green-900/30 border border-green-700/30 text-green-400 text-xs font-medium mb-8">
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
          Live Trading System
        </div>

        <h1 className="text-4xl sm:text-5xl lg:text-7xl font-bold tracking-tight text-white leading-tight mb-6 text-balance">
          Systematic Alpha
          <br />
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-green-400 via-emerald-400 to-teal-400">
            Engineered Daily
          </span>
        </h1>

        <p className="text-lg sm:text-xl text-market-300 max-w-2xl mx-auto mb-10 text-balance">
          A multi-basket trading system combining Base Yield, Alpha, and Convexity
          strategies — built on academic research, executed on IBKR.
        </p>

        <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
          <a
            href="/dashboard"
            className="px-8 py-3.5 rounded-xl bg-green-600 hover:bg-green-500 text-white font-semibold text-base transition-all shadow-lg shadow-green-600/25 hover:shadow-green-500/40"
          >
            View Dashboard
          </a>
          <a
            href="#features"
            className="px-8 py-3.5 rounded-xl border border-market-700 hover:border-market-500 text-market-200 font-medium text-base transition-colors"
          >
            How It Works
          </a>
        </div>
      </div>
    </section>
  )
}
