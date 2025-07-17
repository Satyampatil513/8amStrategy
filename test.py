import asyncio
import websockets
import json

# WebSocket URL (choose testnet or mainnet)
WS_URL = "wss://api.hyperliquid.xyz/ws"  # Use testnet if needed: "wss://api.hyperliquid-testnet.xyz/ws"

# Subscription message to get 1-minute candles for BTC:USDC
subscription_message = {
    "method": "subscribe",
    "subscription": {
        "type": "candle",
        "coin": "BTC:USDC/USDC",
        "interval": "1m"
    }
}

async def listen_to_candles():
    async with websockets.connect(WS_URL) as websocket:
        # Send subscription
        await websocket.send(json.dumps(subscription_message))
        print(f"Subscribed to 1m BTC:USDC candles")

        while True:
            try:
                # Receive messages
                message = await websocket.recv()
                data = json.loads(message)

                # Print only candle updates
                if data.get("channel") == "candle":
                    print("Candle Data:", json.dumps(data, indent=2))
            except websockets.exceptions.ConnectionClosed as e:
                print("WebSocket connection closed:", e)
                break
            except Exception as e:
                print("Error:", e)

if __name__ == "__main__":
    asyncio.run(listen_to_candles())
