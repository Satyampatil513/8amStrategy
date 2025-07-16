import ccxt
import csv
from datetime import datetime, timedelta
import pytz
import time

# === Config ===
IST = pytz.timezone('Asia/Kolkata')
SYMBOL = 'BTC/USDC:USDC'
TIMEFRAME = '5m'
RISK = 5
REWARD = 20
MARGIN = 150
LEVERAGE = 40
MAX_POSITION_VALUE = MARGIN * LEVERAGE
START_HOUR = 8   # 8:00 AM IST
END_HOUR = 24    # 12:00 AM IST (midnight)

# === Candle Analysis ===
def analyze_candle(candle):
    open_, close = candle[1], candle[4]
    if close > open_:
        return 'GREEN'
    elif close < open_:
        return 'RED'
    return 'DOJI'

def find_entry_pattern(candles, day_start):
    for i in range(len(candles) - 1):
        c1 = candles[i]
        c2 = candles[i + 1]
        c1_time = datetime.fromtimestamp(c1[0] / 1000, IST)
        if c1_time < day_start:
            continue
        type1 = analyze_candle(c1)
        type2 = analyze_candle(c2)
        if (type1 == 'GREEN' and type2 == 'RED') or (type1 == 'RED' and type2 == 'GREEN'):
            ts1 = c1_time.strftime('%H:%M')
            ts2 = datetime.fromtimestamp(c2[0] / 1000, IST).strftime('%H:%M')
            high1, low1 = c1[2], c1[3]
            high2, low2 = c2[2], c2[3]
            supermax = max(high1, high2)
            supermin = min(low1, low2)
            candle_range = supermax - supermin
            return {
                'supermax': supermax,
                'supermin': supermin,
                'range': candle_range,
                'timestamp': ts2,
                'entry_time': c2[0]  # ms timestamp of second candle
            }
    return None

def simulate_trade(prices, setup):
    entry, direction = None, None
    for price, ts in prices:
        if price > setup['supermax']:
            entry = setup['supermax']
            direction = 'LONG'
            entry_ts = ts
            break
        elif price < setup['supermin']:
            entry = setup['supermin']
            direction = 'SHORT'
            entry_ts = ts
            break
    if entry is None:
        return None  # No breakout
    range_ = setup['range']
    max_position_size = MAX_POSITION_VALUE
    max_qty = max_position_size / entry
    qty = min(RISK / range_, max_qty)
    stop = entry - range_ if direction == 'LONG' else entry + range_
    target = entry + 4 * range_ if direction == 'LONG' else entry - 4 * range_
    # Simulate after entry
    for price, ts in prices:
        if ts < entry_ts:
            continue
        if direction == 'LONG':
            if price <= stop:
                return {'result': 'SL', 'entry': entry, 'stop': stop, 'target': target, 'qty': qty, 'direction': direction, 'exit_price': price, 'exit_time': ts}
            elif price >= target:
                return {'result': 'TP', 'entry': entry, 'stop': stop, 'target': target, 'qty': qty, 'direction': direction, 'exit_price': price, 'exit_time': ts}
        else:
            if price >= stop:
                return {'result': 'SL', 'entry': entry, 'stop': stop, 'target': target, 'qty': qty, 'direction': direction, 'exit_price': price, 'exit_time': ts}
            elif price <= target:
                return {'result': 'TP', 'entry': entry, 'stop': stop, 'target': target, 'qty': qty, 'direction': direction, 'exit_price': price, 'exit_time': ts}
    return None  # Neither hit

def main():
    dex = ccxt.hyperliquid({'enableRateLimit': True})
    dex.load_markets()
    now = datetime.now(IST)
    start_date = datetime(2025, 6, 29, 0, 0, 0, tzinfo=IST)
    results = []
    account_balance = 150  # Start with $150
    day_count = (now.date() - start_date.date()).days + 1
    for day in range(day_count):
        day_start = start_date + timedelta(days=day, hours=START_HOUR)
        day_end = start_date + timedelta(days=day, hours=END_HOUR)
        since = int(day_start.timestamp() * 1000)
        until = int(day_end.timestamp() * 1000)
        candles = dex.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since, limit=5000)
        if not candles or len(candles) < 2:
            continue
        # Prepare all possible pairs for the day
        trade_count = 0
        sl_losses = 0
        tp_hit = False
        i = 0
        last_exit_index = 0
        while i < len(candles) - 1 and trade_count < 3 and not tp_hit:
            # Only consider pairs after last exit
            if i < last_exit_index:
                i += 1
                continue
            c1 = candles[i]
            c2 = candles[i + 1]
            c1_time = datetime.fromtimestamp(c1[0] / 1000, IST)
            # Only consider pairs where the first candle is at or after START_HOUR
            if c1_time.hour < START_HOUR or (c1_time.hour == START_HOUR and c1_time.minute < 0):
                i += 1
                continue
            type1 = analyze_candle(c1)
            type2 = analyze_candle(c2)
            if (type1 == 'GREEN' and type2 == 'RED') or (type1 == 'RED' and type2 == 'GREEN'):
                high1, low1 = c1[2], c1[3]
                high2, low2 = c2[2], c2[3]
                supermax = max(high1, high2)
                supermin = min(low1, low2)
                candle_range = supermax - supermin
                pattern = {
                    'supermax': supermax,
                    'supermin': supermin,
                    'range': candle_range,
                    'timestamp': datetime.fromtimestamp(c2[0] / 1000, IST).strftime('%H:%M'),
                    'entry_time': c2[0]
                }
                # === Fetch ticker every 1s to find breakout entry ===
                entry, direction, entry_idx = None, None, None
                ticker_found = False
                ticker_time = None
                # Simulate 1s ticker fetching from the time of the last pattern candle to the end of the day
                # We'll use the close price of the next 5m candles as a proxy for 1s ticks (since we can't get real 1s data in backtest)
                # For each 5m candle after c2, simulate 300 ticks (1 per second)
                for j in range(i + 2, len(candles)):
                    c = candles[j]
                    tick_price = c[4]  # Use close price as proxy
                    for tick in range(300):  # 300 seconds in 5m
                        # Simulate tick time
                        ticker_time = c[0] + tick * 1000  # ms
                        if tick_price > pattern['supermax']:
                            entry = pattern['supermax']
                            direction = 'LONG'
                            entry_idx = j
                            ticker_found = True
                            break
                        elif tick_price < pattern['supermin']:
                            entry = pattern['supermin']
                            direction = 'SHORT'
                            entry_idx = j
                            ticker_found = True
                            break
                    if ticker_found:
                        break
                if entry is None:
                    i += 1
                    continue
                range_ = pattern['range']
                max_position_size = MAX_POSITION_VALUE
                max_qty = max_position_size / entry
                qty = min(RISK / range_, max_qty)
                stop = entry - range_ if direction == 'LONG' else entry + range_
                target = entry + 4 * range_ if direction == 'LONG' else entry - 4 * range_
                # Simulate after entry
                result, exit_price, exit_idx = None, None, None
                for price, ts, idx in prices:
                    if entry_idx is not None and idx <= entry_idx:
                        continue
                    if direction == 'LONG':
                        if price <= stop:
                            result = 'SL'
                            exit_price = price
                            exit_idx = idx
                            break
                        elif price >= target:
                            result = 'TP'
                            exit_price = price
                            exit_idx = idx
                            break
                    else:
                        if price >= stop:
                            result = 'SL'
                            exit_price = price
                            exit_idx = idx
                            break
                        elif price <= target:
                            result = 'TP'
                            exit_price = price
                            exit_idx = idx
                            break
                if result is None:
                    # Neither hit, treat as no result, move to next pair
                    i += 1
                    continue
                pnl = REWARD if result == 'TP' else -RISK
                account_balance += pnl
                trade_count += 1
                if result == 'SL':
                    sl_losses += 1
                if result == 'TP':
                    tp_hit = True
                results.append({
                    'date': day_start.strftime('%Y-%m-%d'),
                    'trade_num': trade_count,
                    'pattern_time': pattern['timestamp'],
                    'direction': direction,
                    'entry': entry,
                    'stop': stop,
                    'target': target,
                    'qty': qty,
                    'result': result,
                    'pnl': pnl,
                    'balance': account_balance
                })
                last_exit_index = exit_idx if exit_idx is not None else i + 2
                i = last_exit_index
                if sl_losses >= 3:
                    break
            else:
                i += 1
        # If no trades for the day, record a row
        if trade_count == 0:
            results.append({'date': day_start.strftime('%Y-%m-%d'), 'trade_num': '', 'pattern_time': '', 'direction': '', 'entry': '', 'stop': '', 'target': '', 'qty': '', 'result': 'NoPattern', 'pnl': 0, 'balance': account_balance})
    # Write to CSV
    with open('backtest_results.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['date', 'trade_num', 'pattern_time', 'direction', 'entry', 'stop', 'target', 'qty', 'result', 'pnl', 'balance'])
        writer.writeheader()
        for row in results:
            writer.writerow(row)
    print('Backtest complete. Results saved to backtest_results.csv')

if __name__ == "__main__":
    main() 