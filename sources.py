"""Pluggable trade sources.

The engine depends on TradeSource (the interface), never on a specific
exchange. To add Bybit or point at an FX feed later, you add a class here and
change nothing downstream. This is the "market-agnostic engine" decision.
"""
from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

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
                        t = json.loads(raw)
                        yield Trade(
                            ts_ns=int(t["T"]) * 1_000_000,     # ms -> ns
                            symbol=t["s"],
                            price=float(t["p"]),
                            size=float(t["q"]),
                            side="SELL" if t["m"] else "BUY",  # aggressor
                            trade_id=int(t["t"]),
                        )
            except Exception as e:  # noqa: BLE001
                print(f"[source] reconnecting after error: {e}")
                await asyncio.sleep(2)
