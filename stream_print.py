"""Quick win: live Binance BTCUSDT trades → terminal.

Just proves the pipe works end-to-end: network + websocket + parsing.
No DB yet. Prints 20 trades and exits.

Note on `m` (is-buyer-maker): if the buyer is the maker, the *aggressor*
is the seller → it's an aggressive SELL. That aggressor side is exactly
the order-flow signal you already know from the FX bot.
"""
import asyncio
import json

import websockets

URL = "wss://stream.binance.com:9443/ws/btcusdt@trade"


async def main() -> None:
    async with websockets.connect(URL, ping_interval=20) as ws:
        print("connected → BTCUSDT trades (showing 20)\n")
        n = 0
        async for raw in ws:
            t = json.loads(raw)
            price = float(t["p"])
            qty = float(t["q"])
            side = "SELL" if t["m"] else "BUY"  # aggressor side
            n += 1
            print(f"  {side:4}  {qty:>11.5f} BTC  @  {price:>12,.2f}")
            if n >= 20:
                break
        print(f"\n✔ got {n} live trades — network + websocket work")


if __name__ == "__main__":
    asyncio.run(main())
