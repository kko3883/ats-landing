"""
Live / paper runner -- wires FourLevelStrategy to Interactive Brokers through a
Gateway you already run (the dockerized gnzsnz/ib-gateway on the NAS).

Nautilus's TradingNode reconciles open orders + positions against IB on connect
and via a background loop -- this is the layer the old daemon hand-rolled and got
wrong. (Caveat: see GitHub issue #3655 -- manual closes outside Nautilus can still
be missed for the IB adapter, so test the "close a position in TWS" scenario.)

Targets NautilusTrader v1.227.0 -- verify config field names against your version.

Run:
    export IB_ACCOUNT_ID=DU1234567      # paper account id from IB
    python run_live.py
"""
import os

from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersDataClientConfig,
    InteractiveBrokersExecClientConfig,
    InteractiveBrokersInstrumentProviderConfig,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveDataClientFactory,
    InteractiveBrokersLiveExecClientFactory,
)
from nautilus_trader.config import LoggingConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode

from strategy_four_level import FourLevelConfig, FourLevelStrategy

# -- Instruments (IB FX venue is IDEALPRO) --
# v2: 15-minute bars -- sliding 1H is built internally from 4 x 15m bars.
FX = ["EUR.USD", "AUD.JPY", "NZD.JPY"]
BAR_TYPES = [f"{s}-15-MINUTE-MID-EXTERNAL" for s in FX]

# 4002 = IB Gateway paper, 4001 = live. Start on paper.
IBG_HOST = os.environ.get("IBG_HOST", "127.0.0.1")
IBG_PORT = int(os.environ.get("IBG_PORT", "4002"))

instrument_provider = InteractiveBrokersInstrumentProviderConfig(
    load_ids=frozenset(FX),
)

data_client = InteractiveBrokersDataClientConfig(
    ibg_host=IBG_HOST,
    ibg_port=IBG_PORT,
    ibg_client_id=1,
    instrument_provider=instrument_provider,
)

exec_client = InteractiveBrokersExecClientConfig(
    ibg_host=IBG_HOST,
    ibg_port=IBG_PORT,
    ibg_client_id=1,
    account_id=os.environ["IB_ACCOUNT_ID"],
    instrument_provider=instrument_provider,
)

strategy = FourLevelStrategy(
    FourLevelConfig(
        bar_types=BAR_TYPES,
        position_sizes={
            "EUR.USD": 100_000,
            "AUD.JPY": 250_000,
            "NZD.JPY": 250_000,
        },
        atr_multipliers={
            "EUR.USD": 5.0,
            "AUD.JPY": 10.0,
            "NZD.JPY": 10.0,
        },
    )
)

config = TradingNodeConfig(
    trader_id="ATS-FX-001",
    logging=LoggingConfig(log_level="INFO"),
    data_clients={"IB": data_client},
    exec_clients={"IB": exec_client},
)

node = TradingNode(config=config)
node.add_data_client_factory("IB", InteractiveBrokersLiveDataClientFactory)
node.add_exec_client_factory("IB", InteractiveBrokersLiveExecClientFactory)
node.trader.add_strategy(strategy)


if __name__ == "__main__":
    node.build()
    try:
        node.run()
    finally:
        node.dispose()
