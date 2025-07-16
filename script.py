import ccxt
import time
import logging
from datetime import datetime
import pytz
import asyncio
import json
import websockets

# === Logging ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === Constants ===
IST = pytz.timezone('Asia/Kolkata')
SYMBOL = 'BTC/USDC:USDC'
TIMEFRAME = '5m'
LIMIT = 20

# === Trading Window ===
START_HOUR = 23   # 8:00 AM IST
START_MINUTE = 20
END_HOUR = 23    # 12:00 AM IST (midnight)
END_MINUTE = 59

RISK = 5                # $ per trade
REWARD = 20             # $ per TP
MARGIN = 150            # Daily max margin
LEVERAGE = 40
MAX_POSITION_VALUE = MARGIN * LEVERAGE  # $6000 max trade value

# === Exchange ===
def init_exchange():
    dex = ccxt.hyperliquid({'enableRateLimit': True})
    dex.load_markets()
    return dex

# === Time ===
def is_market_hours():
    now = datetime.now(IST)
    start = now.replace(hour=START_HOUR, minute=START_MINUTE, second=0, microsecond=0)
    end = now.replace(hour=END_HOUR, minute=END_MINUTE, second=0, microsecond=0)
    return start <= now < end

# === Candle Analysis ===
def analyze_candle(candle):
    open_, close = candle[1], candle[4]
    if close > open_:
        return 'GREEN'
    elif close < open_:
        return 'RED'
    return 'DOJI'

# === Pattern Logic ===
def find_entry_pattern(candles):
    for i in range(len(candles) - 1):
        c1 = candles[i]
        c2 = candles[i + 1]
        # Convert c1's timestamp to IST
        c1_time = datetime.fromtimestamp(c1[0] / 1000, IST)
        # Only consider pairs where the first candle is at or after START_HOUR:START_MINUTE
        if (c1_time.hour < START_HOUR or
            (c1_time.hour == START_HOUR and c1_time.minute < START_MINUTE)):
            continue

        type1 = analyze_candle(c1)
        type2 = analyze_candle(c2)

        if (type1 == 'GREEN' and type2 == 'RED') or (type1 == 'RED' and type2 == 'GREEN'):
            ts1 = c1_time.strftime('%H:%M')
            ts2 = datetime.fromtimestamp(c2[0] / 1000, IST).strftime('%H:%M')
            logger.info(f"Pattern found: {type1} → {type2} at {ts1} → {ts2}")
            high1, low1 = c1[2], c1[3]
            high2, low2 = c2[2], c2[3]
            supermax = max(high1, high2)
            supermin = min(low1, low2)
            candle_range = supermax - supermin

            return {
                'supermax': supermax,
                'supermin': supermin,
                'range': candle_range,
                'timestamp': ts2
            }
    return None

async def wait_for_breakout_ws(supermax, supermin, coin="BTC", mainnet=True):
    url = "wss://api.hyperliquid.xyz/ws" if mainnet else "wss://api.hyperliquid-testnet.xyz/ws"
    async with websockets.connect(url) as ws:
        sub_msg = {
            "method": "subscribe",
            "subscription": {"type": "trades", "coin": coin}
        }
        await ws.send(json.dumps(sub_msg))
        logger.info(f"[WebSocket] Subscribed to trades for {coin}")
        async for message in ws:
            data = json.loads(message)
            if data.get("channel") == "trades":
                trades = data.get("data", [])
                for trade in trades:
                    price = float(trade["px"])
                    logger.info(f"[WebSocket] Trade price: {price}")
                    if price > supermax:
                        logger.info(f"[WebSocket] Breakout LONG at {price}")
                        return "LONG", price
                    elif price < supermin:
                        logger.info(f"[WebSocket] Breakout SHORT at {price}")
                        return "SHORT", price

# === Monitor & Simulate Trade ===
def monitor_and_trade(dex, setup, sl_losses, tp_count):
    logger.info(f"Watching for breakout (supermax: {setup['supermax']:.2f}, supermin: {setup['supermin']:.2f}) [WebSocket]")
    entry, direction = None, None

    # === WebSocket for real-time breakout detection ===
    try:
        result = asyncio.run(wait_for_breakout_ws(setup['supermax'], setup['supermin'], coin="BTC"))
        if result is None:
            logger.error("WebSocket returned no breakout result.")
            return sl_losses, tp_count
        direction, entry = result
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        return sl_losses, tp_count

    range_ = setup['range']
    max_position_size = MAX_POSITION_VALUE
    max_qty = max_position_size / entry
    qty = min(RISK / range_, max_qty)

    stop = entry - range_ if direction == 'LONG' else entry + range_
    target = entry + 4 * range_ if direction == 'LONG' else entry - 4 * range_

    logger.info(f"🎯 TRADE ENTERED: {direction}")
    logger.info(f"Entry: {entry:.2f}, SL: {stop:.2f}, TP: {target:.2f}, Qty: {qty:.4f}")
    logger.info(f"Leverage: {LEVERAGE}x | Margin used: ~${entry * qty / LEVERAGE:.2f} (cross margin assumed)")

    # === Simulated Monitoring ===
    while True:
        try:
            ticker = dex.fetch_ticker(SYMBOL)
            price = ticker['last']
            now_time = datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"[1s Monitor] {now_time} Price: {price:.2f}")

            if direction == 'LONG':
                if price <= stop:
                    logger.info("❌ Stop loss hit")
                    sl_losses += 1
                    break
                elif price >= target:
                    logger.info("✅ Take profit hit")
                    tp_count += 1
                    sl_losses = 0  # reset SL streak
                    break
            else:
                if price >= stop:
                    logger.info("❌ Stop loss hit")
                    sl_losses += 1
                    break
                elif price <= target:
                    logger.info("✅ Take profit hit")
                    tp_count += 1
                    sl_losses = 0
                    break

            time.sleep(1)
        except Exception as e:
            logger.error(f"Monitor error: {e}")
            time.sleep(1)

    return sl_losses, tp_count

# === Main Strategy ===
def run_strategy(dex):
    logger.info("Running breakout strategy")

    sl_losses = 0
    tp_count = 0
    pattern_found = False
    setup = None

    while True:
        now = datetime.now(IST)

        if not is_market_hours():
            logger.info("Market closed. Sleeping 5 min.")
            time.sleep(300)
            continue

        # Check if we hit daily stop rule
        if tp_count >= 1 or sl_losses >= 3:
            logger.info(f"📛 DAILY STOP: TP count = {tp_count}, SL count = {sl_losses}")
            time.sleep(300)
            # change to wake up at 8AM
            continue

        if not pattern_found and now.hour >= 8:
            try:
                candles = dex.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=LIMIT)
                setup = find_entry_pattern(candles)
                if setup:
                    pattern_found = True
                    sl_losses, tp_count = monitor_and_trade(dex, setup, sl_losses, tp_count)
                else:
                    logger.info("No pattern yet, retrying in 1 min")
            except Exception as e:
                logger.error(f"Candle fetch error: {e}")
            time.sleep(60)
        else:
            logger.info("Waiting for pattern or 8AM")
            time.sleep(60)

# === Main ===
def main():
    dex = init_exchange()
    run_strategy(dex)

if __name__ == "__main__":
    main()
