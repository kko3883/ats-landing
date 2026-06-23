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
import time

from nautilus_trader.adapters.interactive_brokers.common import IBContract
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
# Nautilus 1.227.0 needs explicit CASH contracts to find FX instruments.
# Without load_contracts, it defaults to STK/SMART and loads nothing.
FX_CONTRACTS = [
    IBContract(secType="CASH", symbol="EUR", currency="USD", exchange="IDEALPRO"),
    IBContract(secType="CASH", symbol="AUD", currency="JPY", exchange="IDEALPRO"),
    IBContract(secType="CASH", symbol="NZD", currency="JPY", exchange="IDEALPRO"),
]
# Instrument IDs Nautilus resolves after loading: EUR/USD.IDEALPRO, AUD/JPY.IDEALPRO, NZD/JPY.IDEALPRO
FX_IDS = frozenset({
    "EUR/USD.IDEALPRO",
    "AUD/JPY.IDEALPRO",
    "NZD/JPY.IDEALPRO",
})
# The strategy receives bar_types with instrument IDs in IDEALPRO venue format
BAR_TYPES = [
    "EUR/USD.IDEALPRO-15-MINUTE-MID-EXTERNAL",
    "AUD/JPY.IDEALPRO-15-MINUTE-MID-EXTERNAL",
    "NZD/JPY.IDEALPRO-15-MINUTE-MID-EXTERNAL",
]

# 4002 = IB Gateway paper, 4001 = live. Start on paper.
IBG_HOST = os.environ.get("IBG_HOST", "127.0.0.1")
IBG_PORT = int(os.environ.get("IBG_PORT", "4002"))

# Use fixed client IDs — the IB Gateway has EXISTING_SESSION_DETECTED_ACTION=primary,
# which means it takes over stale sessions automatically. Unique IDs per restart
# would accumulate orphaned client connections that block future reconnects.
CLIENT_ID_DATA = 1
CLIENT_ID_EXEC = 2

print(f"[ats-fx-daemon] Waiting 5s for gateway stabilisation...")
time.sleep(5)

instrument_provider = InteractiveBrokersInstrumentProviderConfig(
    load_ids=FX_IDS,
    load_contracts=frozenset(FX_CONTRACTS),
)

data_client = InteractiveBrokersDataClientConfig(
    ibg_host=IBG_HOST,
    ibg_port=IBG_PORT,
    ibg_client_id=CLIENT_ID_DATA,
    instrument_provider=instrument_provider,
)

exec_client = InteractiveBrokersExecClientConfig(
    ibg_host=IBG_HOST,
    ibg_port=IBG_PORT,
    ibg_client_id=CLIENT_ID_EXEC,
    account_id=os.environ["IB_ACCOUNT_ID"],
    instrument_provider=instrument_provider,
)

strategy = FourLevelStrategy(
    FourLevelConfig(
        bar_types=BAR_TYPES,
        position_sizes={
            "EUR/USD.IDEALPRO": 100_000,
            "AUD/JPY.IDEALPRO": 250_000,
            "NZD/JPY.IDEALPRO": 250_000,
        },
        atr_multipliers={
            "EUR/USD.IDEALPRO": 5.0,
            "AUD/JPY.IDEALPRO": 10.0,
            "NZD/JPY.IDEALPRO": 10.0,
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
    print(f"[ats-fx-daemon] Fixed client IDs: data={CLIENT_ID_DATA}, exec={CLIENT_ID_EXEC}")
    node.build()
    try:
        node.run()
    finally:
        node.dispose()
