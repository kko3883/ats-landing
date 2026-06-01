import Script from 'next/script'
import '../styles/globals.css'

export default function App({ Component, pageProps }) {
  return (
    <>
      {/* TradingView library — preload so widget script is always ready */}
      <Script
        src="https://s3.tradingview.com/tv.js"
        strategy="beforeInteractive"
      />
      <Component {...pageProps} />
    </>
  )
}
