# FX daemon (NautilusTrader runner) — connects to the IB Gateway container.
FROM python:3.12-slim

WORKDIR /app

# Pin to the version the scaffold was validated against (2026-06-05).
RUN pip install --no-cache-dir "nautilus_trader[ib]==1.227.0"

COPY strategy_four_level.py run_live.py ./

ENV PYTHONUNBUFFERED=1
CMD ["python", "run_live.py"]
