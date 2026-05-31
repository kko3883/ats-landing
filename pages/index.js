import Layout from '../components/Layout'
import Hero from '../components/Hero'
import Features from '../components/Features'
import DashboardPreview from '../components/DashboardPreview'

export default function Home() {
  return (
    <Layout>
      <Hero />
      <Features />
      <DashboardPreview />

      {/* About */}
      <section id="about" className="border-t border-market-800/30">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-20 text-center">
          <h2 className="text-3xl sm:text-4xl font-bold text-white mb-6">About copperplate ATS</h2>
          <div className="text-market-300 space-y-4 text-left leading-relaxed">
            <p>
              copperplate is a systematic multi-basket trading system operating on
              Interactive Brokers. It splits capital across three strategy buckets —
              <strong className="text-white"> Base Yield</strong>, <strong className="text-white">Alpha</strong>, and
              <strong className="text-white"> Convexity</strong> — each targeting a distinct
              return driver.
            </p>
            <p>
              Signals are generated daily through a configurable YAML-driven engine,
              with a screened watchlist, cross-sectional ranking, and VIX-regime
              adjustment. Execution runs on IBKR with position sizing proportional to
              each bucket's allocation.
            </p>
            <p>
              All strategies are grounded in published academic research on factor
              premia, volatility arbitrage, and options-based hedging. The system
              prioritizes transparency — every config is version-controlled, every
              signal is logged, and the dashboard surfaces the full decision chain.
            </p>
          </div>
        </div>
      </section>
    </Layout>
  )
}
