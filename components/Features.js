const strategies = [
  {
    name: 'Base Yield',
    tag: 'Income',
    description: 'Core fixed-income and dividend strategies targeting steady, uncorrelated returns across market regimes.',
    color: 'from-blue-500 to-cyan-500',
    bgGlow: 'bg-blue-500/5',
  },
  {
    name: 'Alpha',
    tag: 'Edge',
    description: 'Factor-based cross-sectional momentum and mean-reversion signals across multi-asset universes.',
    color: 'from-green-500 to-emerald-500',
    bgGlow: 'bg-green-500/5',
  },
  {
    name: 'Convexity',
    tag: 'Tail',
    description: 'Optionality-focused strategies for asymmetric payoffs — capturing upside while limiting downside exposure.',
    color: 'from-purple-500 to-violet-500',
    bgGlow: 'bg-purple-500/5',
  },
]

const features = [
  {
    title: 'Systematic Screener',
    desc: 'Daily watchlist generation with VIX-bucket grouping, cross-sectional ranking, and configurable filters.',
    icon: '📊',
  },
  {
    title: 'YAML-Driven Engine',
    desc: 'Strategy logic defined in YAML config files — not code. Modify parameters, add new signals without touching a line of Python.',
    icon: '⚙️',
  },
  {
    title: 'Real-Time Monitoring',
    desc: 'Live dashboard showing signal status, portfolio snapshot, and order execution — updated via Supabase.',
    icon: '📡',
  },
  {
    title: 'Academic Backing',
    desc: 'Strategies built on published research and validated factor premia — not arbitrary technical indicators.',
    icon: '📐',
  },
]

export default function Features() {
  return (
    <section id="features" className="border-t border-market-800/30">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-24">
        <div className="text-center mb-16">
          <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">Strategy Buckets</h2>
          <p className="text-market-400 max-w-xl mx-auto">
            Three complementary risk premia designed to perform across market cycles.
          </p>
        </div>

        <div className="grid md:grid-cols-3 gap-6 mb-24">
          {strategies.map((s) => (
            <div
              key={s.name}
              className={`relative rounded-2xl border border-market-800/50 ${s.bgGlow} p-6 hover:border-market-700/50 transition-colors group`}
            >
              <div className="flex items-center gap-3 mb-4">
                <div className={`w-3 h-3 rounded-full bg-gradient-to-r ${s.color}`} />
                <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-market-800 text-market-400 uppercase tracking-wider">
                  {s.tag}
                </span>
              </div>
              <h3 className="text-xl font-semibold text-white mb-2">{s.name}</h3>
              <p className="text-sm text-market-300 leading-relaxed">{s.description}</p>
            </div>
          ))}
        </div>

        <div className="text-center mb-12">
          <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">Platform Features</h2>
          <p className="text-market-400 max-w-xl mx-auto">
            Built for automation from day one.
          </p>
        </div>

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {features.map((f) => (
            <div
              key={f.title}
              className="rounded-xl border border-market-800/30 p-5 hover:border-market-700/50 transition-colors"
            >
              <div className="text-2xl mb-3">{f.icon}</div>
              <h3 className="text-sm font-semibold text-white mb-1.5">{f.title}</h3>
              <p className="text-xs text-market-400 leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
