import ccxt
import csv
from datetime import datetime, timedelta
import pytz
import matplotlib.pyplot as plt

# === Config ===
IST = pytz.timezone('Asia/Kolkata')
SYMBOL = 'BTC/USDT'
TIMEFRAME = '5m'
RISK = 5
REWARD = 20
MARGIN = 150
LEVERAGE = 40
MAX_POSITION_VALUE = MARGIN * LEVERAGE
START_HOUR = 8   # 8:00 AM IST
END_HOUR = 23    # 11:00 PM IST (last candle will be 23:55)

# === Candle Analysis ===
def analyze_candle(candle):
    open_, close = candle[1], candle[4]
    if close > open_:
        return 'GREEN'
    elif close < open_:
        return 'RED'
    return 'DOJI'

def fetch_all_ohlcv(dex, symbol, timeframe, since, until):
    """Fetch all OHLCV candles from 'since' to 'until' using pagination."""
    all_candles = []
    limit = 1500
    while since < until:
        candles = dex.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        if not candles:
            break
        # Only keep candles within the 'until' range
        candles = [c for c in candles if c[0] < until]
        if not candles:
            break
        all_candles.extend(candles)
        last_ts = candles[-1][0]
        since = last_ts + 1
        if len(candles) < limit:
            break
    if all_candles:
        first_candle = all_candles[0]
        last_candle = all_candles[-1]
        first_time = datetime.fromtimestamp(first_candle[0] / 1000, IST).strftime('%Y-%m-%d %H:%M')
        last_time = datetime.fromtimestamp(last_candle[0] / 1000, IST).strftime('%Y-%m-%d %H:%M')
        print(f"First candle: {first_time}, Last candle: {last_time}")
    expected_candles = int((until - since) / (5 * 60 * 1000))
    if len(all_candles) < expected_candles * 0.8:  # less than 80% of expected
        print(f"Warning: Only fetched {len(all_candles)} candles, expected about {expected_candles}. Data may be limited by Binance.")
    return all_candles

def main():
    dex = ccxt.binance({'enableRateLimit': True})
    dex.load_markets()
    now = datetime.now(IST)
    start_date = (now - timedelta(days=90)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    since = int(start_date.timestamp() * 1000)
    until = int(end_date.timestamp() * 1000)
    print(f"Fetching 5m candles from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...")
    candles = fetch_all_ohlcv(dex, SYMBOL, TIMEFRAME, since, until)
    print(f"Fetched {len(candles)} candles.")
    # Organize candles by day
    candles_by_day = {}
    for c in candles:
        c_time = datetime.fromtimestamp(c[0] / 1000, IST)
        day_str = c_time.strftime('%Y-%m-%d')
        if day_str not in candles_by_day:
            candles_by_day[day_str] = []
        candles_by_day[day_str].append(c)
    results = []
    account_balance = 150  # Start with $150
    for day_str in sorted(candles_by_day.keys()):
        day_candles = candles_by_day[day_str]
        # Set up day_start and day_end for filtering
        day_start = IST.localize(datetime.strptime(day_str, '%Y-%m-%d')).replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
        day_end = IST.localize(datetime.strptime(day_str, '%Y-%m-%d')).replace(hour=END_HOUR, minute=55, second=0, microsecond=0)
        # Filter candles for trading window
        day_candles = [c for c in day_candles if day_start.timestamp() * 1000 <= c[0] <= day_end.timestamp() * 1000]
        if len(day_candles) < 2:
            continue
        trade_count = 0
        sl_losses = 0
        tp_hit = False
        i = 0
        last_exit_index = 0
        while i < len(day_candles) - 1 and trade_count < 3 and not tp_hit:
            if i < last_exit_index:
                i += 1
                continue
            c1 = day_candles[i]
            c2 = day_candles[i + 1]
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
                prices = [(c[4], c[0], idx) for idx, c in enumerate(day_candles)]
                entry, direction, entry_idx = None, None, None
                for price, ts, idx in prices:
                    if idx <= i + 1:
                        continue
                    if price > pattern['supermax']:
                        entry = pattern['supermax']
                        direction = 'LONG'
                        entry_idx = idx
                        break
                    elif price < pattern['supermin']:
                        entry = pattern['supermin']
                        direction = 'SHORT'
                        entry_idx = idx
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
                    'date': day_str,
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
        if trade_count == 0:
            results.append({'date': day_str, 'trade_num': '', 'pattern_time': '', 'direction': '', 'entry': '', 'stop': '', 'target': '', 'qty': '', 'result': 'NoPattern', 'pnl': 0, 'balance': account_balance})
    with open('backtest_binance_results.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['date', 'trade_num', 'pattern_time', 'direction', 'entry', 'stop', 'target', 'qty', 'result', 'pnl', 'balance'])
        writer.writeheader()
        for row in results:
            writer.writerow(row)
    print('Backtest complete. Results saved to backtest_binance_results.csv')

    # Plot PnL chart
    balances = [row['balance'] for row in results if row['balance'] != '']
    dates = [row['date'] + (f" T{row['trade_num']}" if row['trade_num'] else '') for row in results]
    plt.figure(figsize=(12, 6))
    plt.plot(dates, balances, marker='o')
    plt.title('Account Balance (PnL) Over Time - Binance Backtest')
    plt.xlabel('Trade')
    plt.ylabel('Account Balance ($)')
    plt.xticks(rotation=90, fontsize=7)
    plt.tight_layout()
    plt.grid(True)
    plt.savefig('backtest_binance_pnl.png')
    plt.close()

if __name__ == "__main__":
    main() 