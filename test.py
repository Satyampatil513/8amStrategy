import asyncio
import websockets
import json
from typing import TypedDict

class CandleSubscription(TypedDict):
    type: str
    coin: str
    interval: str

class SubscriptionMessage(TypedDict):
    method: str
    subscription: CandleSubscription

WS_URL = "wss://api.hyperliquid.xyz/ws"

def get_subscription_msg(coin: str, interval: str) -> SubscriptionMessage:
    return {
        "method": "subscribe",
        "subscription": {
            "type": "candle",
            "coin": coin,
            "interval": interval
        }
    }

async def listen_candles(coin: str = "BTC", interval: str = "5m"):
    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        sub_msg = get_subscription_msg(coin, interval)
        await ws.send(json.dumps(sub_msg))
        print(f"✅ Subscribed to {interval} candles for {coin}\n")

        try:
            while True:
                msg = await ws.recv()
                data = json.loads(msg)

                channel = data.get("channel")
                if channel == "subscriptionResponse":
                    print("🟢 Subscription acknowledged.")
                elif channel == "candle":
                    print("📈 Candle update:", json.dumps(data["data"], indent=2))
                else:
                    print("📦 Other message:", json.dumps(data, indent=2))
        except websockets.exceptions.ConnectionClosed as e:
            print("🔴 Connection closed:", e)

if __name__ == "__main__":
    asyncio.run(listen_candles())
