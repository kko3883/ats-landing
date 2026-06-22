"""
IB Gateway bridge via ib_insync.

Connects to the NAS IB Gateway over Tailscale.  Handles:
  - Order placement (market/limit)
  - Order cancellation
  - Position/portfolio queries
  - Fills and P&L tracking

ib_insync is asyncio-native — runs in an event loop alongside the engine.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from ib_insync import IB, Stock, MarketOrder, LimitOrder, Order, Position, PortfolioItem
    HAVE_IB_INSYNC = True
except ImportError:
    HAVE_IB_INSYNC = False
    logger.warning(
        "ib_insync not installed — execution will be simulated (paper mock). "
        "pip install ib_insync for live/paper trading via IB Gateway."
    )


@dataclass
class BrokerPosition:
    """Normalized position from broker."""
    symbol: str
    side: str                    # "LONG" or "SHORT"
    quantity: int
    avg_cost: float
    market_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


@dataclass
class OrderConfirmation:
    """Confirmation of a submitted order."""
    symbol: str
    order_id: str
    side: str
    order_type: str            # "MKT" or "LMT"
    quantity: int
    fill_price: float = 0.0
    status: str = "PENDING"    # PENDING, FILLED, CANCELED, REJECTED
    message: str = ""
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class IBBridge:
    """
    ib_insync wrapper for order execution and portfolio queries.

    If ib_insync is not available, falls back to a mock that logs orders
    but does not execute (safe for development/testing).
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 100,
        account_id: str = "",
    ):
        self._host = host
        self._port = port
        self._client_id = client_id
        self._account_id = account_id
        self._ib: Optional["IB"] = None
        self._connected = False
        self._orders: list[OrderConfirmation] = []
        self._mock_positions: dict[str, BrokerPosition] = {}

    # ── Connection ─────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Connect to IB Gateway.  Returns True on success."""
        if not HAVE_IB_INSYNC:
            logger.info("ib_insync not available — using mock bridge")
            self._connected = True
            return True

        try:
            self._ib = IB()
            await self._ib.connectAsync(
                host=self._host,
                port=self._port,
                clientId=self._client_id,
                timeout=10,
            )
            self._connected = True
            logger.info(
                f"Connected to IB Gateway: {self._host}:{self._port} "
                f"client={self._client_id}"
            )
            return True
        except Exception as e:
            logger.error(f"IB Gateway connection failed: {e}")
            self._connected = False
            return False

    async def disconnect(self):
        """Disconnect from IB Gateway."""
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
        self._connected = False
        logger.info("IB Gateway disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Order placement ───────────────────────────────────────────────

    async def place_market_order(
        self,
        symbol: str,
        side: str,            # "BUY" or "SELL"
        quantity: int,
    ) -> OrderConfirmation:
        """
        Place a market order.  Returns OrderConfirmation with fill details.
        """
        if not HAVE_IB_INSYNC or not self._ib:
            return self._mock_order(symbol, side, "MKT", quantity)

        try:
            contract = self._make_contract(symbol)
            order = MarketOrder(side.upper(), quantity)
            trade = await self._ib.placeOrderAsync(contract, order)

            confirm = OrderConfirmation(
                symbol=symbol,
                order_id=str(trade.order.orderId),
                side=side,
                order_type="MKT",
                quantity=quantity,
                fill_price=float(trade.orderStatus.avgFillPrice or 0),
                status="FILLED" if trade.orderStatus.status == "Filled" else "PENDING",
            )
            self._orders.append(confirm)
            logger.info(f"MKT ORDER: {symbol} {side} {quantity} → {confirm.status}")
            return confirm
        except Exception as e:
            logger.error(f"Market order failed: {symbol} {side} {quantity}: {e}")
            return OrderConfirmation(
                symbol=symbol, order_id="ERR", side=side, order_type="MKT",
                quantity=quantity, status="REJECTED", message=str(e),
            )

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        limit_price: float,
    ) -> OrderConfirmation:
        """Place a limit order."""
        if not HAVE_IB_INSYNC or not self._ib:
            return self._mock_order(symbol, side, "LMT", quantity, limit_price)

        try:
            contract = self._make_contract(symbol)
            order = LimitOrder(side.upper(), quantity, limit_price)
            trade = await self._ib.placeOrderAsync(contract, order)

            confirm = OrderConfirmation(
                symbol=symbol,
                order_id=str(trade.order.orderId),
                side=side,
                order_type="LMT",
                quantity=quantity,
                fill_price=float(trade.orderStatus.avgFillPrice or limit_price),
                status="FILLED" if trade.orderStatus.status == "Filled" else "PENDING",
            )
            self._orders.append(confirm)
            logger.info(f"LMT ORDER: {symbol} {side} {quantity} @ {limit_price} → {confirm.status}")
            return confirm
        except Exception as e:
            logger.error(f"Limit order failed: {symbol} {side} {quantity} @ {limit_price}: {e}")
            return OrderConfirmation(
                symbol=symbol, order_id="ERR", side=side, order_type="LMT",
                quantity=quantity, status="REJECTED", message=str(e),
            )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID."""
        if not HAVE_IB_INSYNC or not self._ib:
            logger.info(f"MOCK cancel: {order_id}")
            return True
        try:
            trades = self._ib.trades()
            for trade in trades:
                if str(trade.order.orderId) == order_id:
                    await self._ib.cancelOrderAsync(trade.order)
                    logger.info(f"Canceled order: {order_id}")
                    return True
            logger.warning(f"Order not found for cancel: {order_id}")
            return False
        except Exception as e:
            logger.error(f"Cancel failed: {order_id}: {e}")
            return False

    async def cancel_all_orders(self):
        """Cancel all open orders."""
        if not HAVE_IB_INSYNC or not self._ib:
            logger.info("MOCK: cancel all orders")
            return
        for trade in self._ib.trades():
            if trade.orderStatus.status not in ("Filled", "Cancelled"):
                try:
                    await self._ib.cancelOrderAsync(trade.order)
                    logger.info(f"Canceled: {trade.contract.symbol}")
                except Exception as e:
                    logger.error(f"Cancel failed: {trade.contract.symbol}: {e}")

    # ── Portfolio & positions ──────────────────────────────────────────

    async def get_positions(self) -> list[BrokerPosition]:
        """Get current positions from broker."""
        if not HAVE_IB_INSYNC or not self._ib:
            return list(self._mock_positions.values())

        try:
            positions = []
            ib_positions = await self._ib.reqPositionsAsync()
            for pos in ib_positions:
                symbol = pos.contract.symbol
                if pos.contract.currency == "USD" and pos.contract.secType == "STK":
                    positions.append(BrokerPosition(
                        symbol=symbol,
                        side="LONG" if pos.position > 0 else "SHORT",
                        quantity=abs(int(pos.position)),
                        avg_cost=float(pos.avgCost or 0),
                        market_price=float(pos.marketPrice or 0),
                        unrealized_pnl=float(pos.unrealizedPNL or 0),
                        realized_pnl=float(pos.realizedPNL or 0),
                    ))
            return positions
        except Exception as e:
            logger.error(f"Position query failed: {e}")
            return []

    async def get_account_equity(self) -> float:
        """Get current account equity (net liquidation value)."""
        if not HAVE_IB_INSYNC or not self._ib:
            return 100_000.0  # mock default

        try:
            summary = await self._ib.reqAccountSummaryAsync()
            for item in summary:
                if item.tag == "NetLiquidation" and item.currency == "USD":
                    return float(item.value)
            return 100_000.0
        except Exception as e:
            logger.error(f"Account summary failed: {e}")
            return 100_000.0

    # ── Internal helpers ───────────────────────────────────────────────

    def _make_contract(self, symbol: str):
        """Create an IB Stock contract from a symbol string e.g. 'AAPL.US'."""
        if not HAVE_IB_INSYNC:
            return None
        ticker = symbol.replace(".US", "").replace(".HK", "")
        contract = Stock(ticker, "SMART", "USD")
        # For HK stocks
        if ".HK" in symbol:
            contract.currency = "HKD"
            contract.exchange = "SEHK"
        return contract

    def _mock_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: int,
        limit_price: float = 0.0,
    ) -> OrderConfirmation:
        """Mock order for when ib_insync is not available."""
        import uuid
        confirm = OrderConfirmation(
            symbol=symbol,
            order_id=f"mock-{uuid.uuid4().hex[:8]}",
            side=side,
            order_type=order_type,
            quantity=quantity,
            fill_price=limit_price if limit_price > 0 else 100.0,
            status="FILLED",  # mock always fills immediately
        )
        self._orders.append(confirm)

        # Update mock positions
        if side == "BUY":
            if symbol in self._mock_positions:
                p = self._mock_positions[symbol]
                p.quantity += quantity
            else:
                self._mock_positions[symbol] = BrokerPosition(
                    symbol=symbol, side="LONG", quantity=quantity,
                    avg_cost=confirm.fill_price,
                )
        else:
            if symbol in self._mock_positions:
                p = self._mock_positions[symbol]
                p.quantity = max(0, p.quantity - quantity)
                if p.quantity == 0:
                    del self._mock_positions[symbol]

        logger.info(f"MOCK ORDER: {symbol} {side} {quantity} {'MKT' if order_type == 'MKT' else f'LMT@{limit_price}'}")
        return confirm