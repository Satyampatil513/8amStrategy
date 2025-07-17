import ccxt
import time
import logging
from datetime import datetime, timedelta
import pytz
import asyncio
import json
import websockets
import os
from dotenv import load_dotenv

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
START_HOUR = 8   # 8:00 AM IST
START_MINUTE = 0
END_HOUR = 23    # 12:00 AM IST (midnight)
END_MINUTE = 59

RISK = 2.0                # $ per trade
REWARD = 8.0             # $ per TP
MARGIN = 100            # Daily max margin
LEVERAGE = 40
MAX_POSITION_VALUE = MARGIN * LEVERAGE  # $6000 max trade value

# === Load environment variables ===
# Make sure to create a .env file in this directory with WALLET_ADDRESS and PRIVATE_KEY
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS')
PRIVATE_KEY = os.getenv('PRIVATE_KEY')

# === Exchange ===
def init_exchange():
    if not WALLET_ADDRESS or not PRIVATE_KEY:
        raise ValueError("WALLET_ADDRESS and PRIVATE_KEY must be set in the .env file")
    dex = ccxt.hyperliquid({
        'enableRateLimit': True,
        'walletAddress': WALLET_ADDRESS,
        'privateKey': PRIVATE_KEY
    })
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
    # Get session start datetime
    # wrong logic, why collecting previous candles. change it to websocket
    now = datetime.now(IST)
    session_start = now.replace(hour=START_HOUR, minute=START_MINUTE, second=0, microsecond=0)
    # Filter candles after or equal to session start
    filtered = [c for c in candles if datetime.fromtimestamp(c[0] / 1000, IST) >= session_start]
    # Need at least 2 closed candles
    if len(filtered) < 3:
        return None
    # Last two closed candles (skip the last, which may be incomplete)
    c1 = filtered[-3]
    c2 = filtered[-2]
    type1 = analyze_candle(c1)
    type2 = analyze_candle(c2)
    if (type1 == 'GREEN' and type2 == 'RED') or (type1 == 'RED' and type2 == 'GREEN'):
        ts1 = datetime.fromtimestamp(c1[0] / 1000, IST).strftime('%H:%M')
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

async def breakout_and_monitor_ws(dex, supermax, supermin, sl_losses, tp_count, coin="BTC", mainnet=True):
    url = "wss://api.hyperliquid.xyz/ws" if mainnet else "wss://api.hyperliquid-testnet.xyz/ws"
    entry = None
    direction = None
    trade_active = False
    stop = None
    target = None
    range_ = None
    qty = None
    async with websockets.connect(url) as ws:
        sub_msg = {
            "method": "subscribe",
            "subscription": {"type": "trades", "coin": coin}
        }
        # Globally subscribe why again and again
        await ws.send(json.dumps(sub_msg))
        logger.info(f"[WebSocket] Subscribed to trades for {coin}")
        async for message in ws:
            data = json.loads(message)
            if data.get("channel") == "trades":
                trades = data.get("data", [])
                for trade in trades:
                    price = float(trade["px"])
                    now_time = datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
                    # Remove noisy ticker logs
                    # logger.info(f"[WebSocket] Trade price: {price}")
                    if not trade_active:
                        if price > supermax:
                            direction = "LONG"
                            entry = price
                        elif price < supermin:
                            direction = "SHORT"
                            entry = price
                        if direction and entry:
                            logger.info(f"[WebSocket] Breakout {direction} at {entry}")
                            # Calculate trade params
                            range_ = supermax - supermin
                            max_position_size = MAX_POSITION_VALUE
                            max_qty = max_position_size / entry
                            qty = min(RISK / range_, max_qty)
                            # --- Correct SL/TP logic ---
                            if direction == 'LONG':
                                stop = entry - range_  #supermin
                                target = entry + 4 * range_   # entry + 4*(entry - supermin)
                            else:  # SHORT
                                stop = entry + range_  #supermax
                                target = entry - 4 * range_  # entry - 4*(supermax - entry)
                            # === Place actual trade ===
                            try:
                                # can done by websocket ?
                                order = dex.create_order(
                                    SYMBOL,
                                    "market",
                                    "buy" if direction == "LONG" else "sell",
                                    qty,
                                    price=entry  # Use the breakout price as reference
                                )
                                logger.info(f"ORDER PLACED: {order}")
                                # Place TP order
                                tp_order = dex.create_order(
                                    SYMBOL,
                                    "limit",
                                    "sell" if direction == "LONG" else "buy",
                                    qty,
                                    price=target
                                )
                                # Place SL order (stop)
                                sl_order = dex.create_order(
                                    SYMBOL,
                                    "stop",
                                    "sell" if direction == "LONG" else "buy",
                                    qty,
                                    price=stop
                                )
                                logger.info(f"TP ORDER: {tp_order}")
                                logger.info(f"SL ORDER: {sl_order}")
                            except Exception as e:
                                logger.error(f"Order placement error: {e}")
                                return sl_losses, tp_count
                            # Save position info if needed
                            position = {
                                "direction": direction,
                                "entry": entry,
                                "qty": qty,
                                "stop": stop,
                                "target": target
                            }
                            trade_active = True
                    else:
                        # Remove noisy monitor logs
                        # logger.info(f"[1s Monitor] {now_time} Price: {price:.2f}")
                        if direction == 'LONG' and stop:
                            if price <= stop:
                                logger.info("❌ Stop loss hit")
                                sl_losses += 1
                                return sl_losses, tp_count
                            elif target and price >= target:
                                logger.info("✅ Take profit hit")
                                tp_count += 1
                                sl_losses = 0  # reset SL streak
                                return sl_losses, tp_count
                        else:
                            if stop and price >= stop:
                                logger.info("❌ Stop loss hit")
                                sl_losses += 1
                                return sl_losses, tp_count
                            elif target and price <= target:
                                logger.info("✅ Take profit hit")
                                tp_count += 1
                                sl_losses = 0
                                return sl_losses, tp_count
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
            # change it to START HOUR , if not reset this variablws
            time.sleep(300)
            continue

        # Check if we hit daily stop rule, we can move it bepw
        if tp_count >= 1 or sl_losses >= 3:
            logger.info(f"📛 DAILY STOP: TP count = {tp_count}, SL count = {sl_losses}")
            time.sleep(300)
            # change to wake up atSTART_HOUR
            continue

        session_start = now.replace(hour=START_HOUR, minute=START_MINUTE, second=0, microsecond=0) 
        minutes_since_start = int((now - session_start).total_seconds() // 60)
        if minutes_since_start < 0:
            # Wait until session start
            sleep_seconds = int((session_start - now).total_seconds())
            logger.info(f"Waiting for session start at {session_start.strftime('%H:%M')}")
            time.sleep(sleep_seconds)
        else:
            # Calculate minutes to next 5-min mark
            mins_to_next = 5 - (minutes_since_start % 5)
            next_candle_time = now + timedelta(minutes=mins_to_next)
            sleep_seconds = (next_candle_time.replace(second=0, microsecond=0) - now).total_seconds()
            logger.info(f"Waiting {int(sleep_seconds)} seconds for next 5-min candle close at {next_candle_time.strftime('%H:%M')}")
            time.sleep(sleep_seconds)
            # Then check for pattern
            try:
                # Calculate timestamp for today at session start
                start_dt = now.replace(hour=START_HOUR, minute=START_MINUTE, second=0, microsecond=0)
                start_ts = int(start_dt.timestamp() * 1000)
                candles = dex.fetch_ohlcv(SYMBOL, TIMEFRAME, since=start_ts, limit=LIMIT)  #fetching, why limit 20
                setup = find_entry_pattern(candles)
                if setup:
                    pattern_found = True
                    # Use websocket for both breakout and monitoring
                    sl_losses, tp_count = asyncio.run(breakout_and_monitor_ws(dex, setup['supermax'], setup['supermin'], sl_losses, tp_count, coin="BTC"))
                else:
                    logger.info("No pattern yet, retrying in 1 min")
            except Exception as e:
                logger.error(f"Candle fetch error: {e}")
            time.sleep(60)

# === Main ===
def main():
    dex = init_exchange()
    run_strategy(dex)

if __name__ == "__main__":
    main()
