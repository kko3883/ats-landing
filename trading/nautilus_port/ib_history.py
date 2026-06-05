#!/usr/bin/env python3
"""
Pull recent executions/fills from IB via the gateway.

IB's reqExecutions returns roughly the CURRENT trading day only — for full
history use the IBKR Client Portal (Activity > Trades) or a Flex Query.

Run it on the NAS docker network so the hostname `ib-gateway` resolves:

  sudo docker run --rm --network nautilus_port_default \
    -v /volume1/docker/ats-landing/trading/nautilus_port:/app \
    -e IBG_HOST=ib-gateway -e IBG_PORT=4004 \
    python:3.12-slim sh -c "pip install -q ib_async && python /app/ib_history.py"
"""
import os
import random

import ib_async as ib

HOST = os.environ.get("IBG_HOST", "ib-gateway")
PORT = int(os.environ.get("IBG_PORT", "4004"))
IB_SENTINEL = 1e17  # IB uses ~1.8e308 to mean "no realized PnL" (an opening fill)

app = ib.IB()
app.connect(HOST, PORT, clientId=random.randint(100, 9999), timeout=20)
try:
    fills = app.reqExecutions()
    if not fills:
        print("No executions returned (IB serves ~the current trading day only).")
    total_pnl = 0.0
    for f in sorted(fills, key=lambda x: str(x.execution.time)):
        e = f.execution
        pnl = getattr(f.commissionReport, "realizedPNL", None)
        pnl_str = ""
        if pnl is not None and pnl == pnl and abs(pnl) < IB_SENTINEL:
            total_pnl += pnl
            pnl_str = f"  realizedPnL={pnl:.2f}"
        print(f"{e.time}  {e.side:3} {e.shares:>10} {f.contract.localSymbol:8} @ {e.price}{pnl_str}")
    if total_pnl:
        print(f"\nSum of realized PnL on today's closing fills: {total_pnl:.2f}")
finally:
    app.disconnect()
