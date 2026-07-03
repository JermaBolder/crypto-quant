"""Pluggable trade sources.

The engine depends on TradeSource (the interface), never on a specific
exchange. To add Bybit or point at an FX feed later, you add a class here and
change nothing downstream. This is the "market-agnostic engine" decision.
"""
from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

import websockets


@dataclass
class Trade:
    ts_ns: int      # event time, nanoseconds
    symbol: str
    price: float
    size: float
    side: str       # "BUY"/"SELL" = aggressor side (order flow)
    trade_id: int


class TradeSource(ABC):
    """Yields a stream of normalized Trades, forever."""

    @abstractmethod
    def stream(self) -> AsyncIterator[Trade]:
        ...


def parse_trade(msg: dict) -> Trade:
    """One Binance @trade payload -> normalized Trade (T is ms; m = buyer-is-maker)."""
    return Trade(
        ts_ns=int(msg["T"]) * 1_000_000,     # ms -> ns
        symbol=msg["s"],
        price=float(msg["p"]),
        size=float(msg["q"]),
        side="SELL" if msg["m"] else "BUY",  # aggressor
        trade_id=int(msg["t"]),
    )


class BinanceTradeSource(TradeSource):
    def __init__(self, symbol: str = "btcusdt") -> None:
        self.symbol = symbol.lower()
        self.url = f"wss://stream.binance.com:9443/ws/{self.symbol}@trade"

    async def stream(self) -> AsyncIterator[Trade]:
        # auto-reconnect: an ingest daemon must survive websocket drops
        while True:
            try:
                async with websockets.connect(self.url, ping_interval=20) as ws:
                    async for raw in ws:
                        yield parse_trade(json.loads(raw))
            except Exception as e:  # noqa: BLE001
                print(f"[source] reconnecting after error: {e}")
                await asyncio.sleep(2)
