export default function Layout({ children }) {
  return (
    <div className="min-h-screen bg-market-950 text-market-100 flex flex-col">
      <header className="sticky top-0 z-50 bg-market-950/80 backdrop-blur-md border-b border-market-800/50">
        <nav className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-green-500 to-emerald-700 flex items-center justify-center text-white font-bold text-xs">
              ATS
            </div>
            <span className="font-semibold text-lg tracking-tight text-white">copperplate</span>
          </div>

          <div className="flex items-center gap-6">
            <a href="#features" className="text-sm text-market-200 hover:text-white transition-colors hidden sm:block">Strategy</a>
            <a href="#dashboard" className="text-sm text-market-200 hover:text-white transition-colors hidden sm:block">Dashboard</a>
            <a href="#about" className="text-sm text-market-200 hover:text-white transition-colors hidden sm:block">About</a>
            <a
              href="/dashboard"
              className="px-4 py-2 rounded-lg bg-green-600 hover:bg-green-500 text-white text-sm font-medium transition-colors"
            >
              Dashboard
            </a>
          </div>
        </nav>
      </header>

      <main className="flex-1">
        {children}
      </main>

      <footer className="border-t border-market-800/50">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2 text-sm text-market-400">
            <div className="w-5 h-5 rounded bg-gradient-to-br from-green-500 to-emerald-700 flex items-center justify-center text-white font-bold text-[8px]">A</div>
            <span>copperplate ATS</span>
          </div>
          <div className="text-xs text-market-500">
            &copy; {new Date().getFullYear()} copperplate.win
          </div>
        </div>
      </footer>
    </div>
  )
}
