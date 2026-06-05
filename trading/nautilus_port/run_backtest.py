"""
Backtest skeleton — the capability the old daemon never had.

Feed historical 1h FX bars and run the SAME FourLevelStrategy, unchanged, that
trades live. This is how you validate the 4-level system BEFORE risking money
(ties into STRATEGIC_REVIEW.md). Fill in the data-loading section with your bars.

Targets NautilusTrader v1.227.0 — verify signatures against your version.
"""
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money

from strategy_four_level import FourLevelConfig, FourLevelStrategy

engine = BacktestEngine(
    config=BacktestEngineConfig(
        trader_id="BT-001",
        logging=LoggingConfig(log_level="ERROR"),
    )
)

IDEALPRO = Venue("IDEALPRO")
engine.add_venue(
    venue=IDEALPRO,
    oms_type=OmsType.NETTING,      # IB FX nets positions
    account_type=AccountType.MARGIN,
    base_currency=USD,
    starting_balances=[Money(100_000, USD)],
)

# ── TODO: add instruments + bars ──────────────────────────────────────────
# from nautilus_trader.test_kit.providers import TestInstrumentProvider
# eurusd = TestInstrumentProvider.default_fx_ccy("EUR/USD", venue=IDEALPRO)
# engine.add_instrument(eurusd)
#
# Parse your 1h OHLCV (the same yfinance bars the daemon used, or IB historical)
# into nautilus Bar objects via a BarDataWrangler, then:
# engine.add_data(bars)
# --------------------------------------------------------------------------

engine.add_strategy(
    FourLevelStrategy(
        FourLevelConfig(
            bar_types=["EUR/USD.IDEALPRO-1-HOUR-MID-EXTERNAL"],
            position_sizes={"EUR/USD.IDEALPRO": 100_000},
            atr_multipliers={"EUR/USD.IDEALPRO": 5.0},
        )
    )
)


if __name__ == "__main__":
    engine.run()
    print(engine.trader.generate_account_report(IDEALPRO))
    print(engine.trader.generate_positions_report())
    print(engine.trader.generate_order_fills_report())
    engine.dispose()
