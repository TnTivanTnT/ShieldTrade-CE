import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import time
import json
import os
import csv
import glob
from datetime import datetime, timedelta

# --- USER CONFIGURATION ---
SIMULATION = True
PAIRS = ['SOL/EUR', 'ETH/EUR']
TOTAL_BUDGET = 50.0
SLOT_SIZE = 10.5
MAX_SLOTS = int(TOTAL_BUDGET // SLOT_SIZE)

# Strategy: DCA + Filter + Trailing Take Profit
RSI_ENTRY_MAX = 45
DCA_REPURCHASE_DROP = 0.03
PROFIT_MARGIN = 0.015
TRAILING_GAP = 0.003       # 0.3% drop from peak to trigger sell
BINANCE_FEE = 0.001        # 0.1% Spot Fee
LOG_RETENTION_DAYS = 15

# --- CREDENTIALS ---
TELEGRAM_TOKEN = "YOUR_TOKEN_HERE"
TELEGRAM_CHAT_ID = "YOUR_ID_HERE"
API_KEY = ''
SECRET_KEY = ''

# --- PATHS ---
CSV_FILE = "/app/trading_history.csv"
STATE_FILE = "/app/bot_state.json"
LOGS_FOLDER = "/app/logs"

# --- INITIALIZATION ---
exchange = ccxt.binance({'enableRateLimit': True})
if not SIMULATION:
    exchange.apiKey = API_KEY
    exchange.secret = SECRET_KEY

def write_daily_log(message):
    try:
        os.makedirs(LOGS_FOLDER, exist_ok=True)
        file_name = f"{LOGS_FOLDER}/log_{datetime.now().strftime('%Y-%m-%d')}.txt"
        with open(file_name, "a", encoding="utf-8") as f:
            f.write(f"{message}\n")
            
        # Log rotation: clean old files
        retention_limit = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
        log_files = glob.glob(f"{LOGS_FOLDER}/log_*.txt")
        for file in log_files:
            date_str = file.split('_')[-1].replace('.txt', '')
            try:
                file_date = datetime.strptime(date_str, '%Y-%m-%d')
                if file_date < retention_limit:
                    os.remove(file)
            except: pass
    except Exception as e: 
        print(f"[ERROR] Logging system failed: {e}")

try:
    exchange.load_markets()
except Exception as e:
    msg = f"[ERROR] Notice: Could not load markets initially: {e}"
    print(msg)
    write_daily_log(msg)

price_cache = {pair: 0.0 for pair in PAIRS}

# --- STATE MANAGER ---
class StateManager:
    def __init__(self):
        self.data = {
            "initial_balance": TOTAL_BUDGET,
            "yesterday_balance": TOTAL_BUDGET,
            "last_report_date": "",
            "total_realized_profit": 0.0,
            "portfolio": {pair: {"amount": 0.0, "avg_price": 0.0, "invested": 0.0, "max_price_reached": 0.0} for pair in PAIRS}
        }
        self.load()

    def load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    loaded_data = json.load(f)
                    for k, v in loaded_data.items():
                        if k == "portfolio":
                            for pair, info in v.items():
                                if "max_price_reached" not in info:
                                    info["max_price_reached"] = 0.0
                        self.data[k] = v
                    if "total_realized_profit" not in self.data:
                        self.data["total_realized_profit"] = 0.0
            except:
                msg = "[ERROR] JSON Error, starting from scratch."
                print(msg)
                write_daily_log(msg)

    def save(self):
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            msg = f"[ERROR] JSON SAVE ERROR: {e}"
            print(msg)
            write_daily_log(msg)

    def get_used_slots(self):
        total_invested = sum(d['invested'] for d in self.data['portfolio'].values())
        return int(total_invested // SLOT_SIZE)

state = StateManager()

# --- UTILITIES ---
def send_telegram_msg(message):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except: pass

def calculate_total_equity():
    free_cash = (TOTAL_BUDGET + state.data['total_realized_profit']) - sum(d['invested'] for d in state.data['portfolio'].values())
    coin_value = sum(info['amount'] * price_cache.get(pair, 0.0) for pair, info in state.data['portfolio'].items() if info['amount'] > 0)
    return free_cash + coin_value

def save_to_history(action, pair, price, amount, total, max_price_trade=0.0):
    try:
        exists = os.path.exists(CSV_FILE)
        with open(CSV_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(["Date", "Pair", "Action", "Price", "Amount", "Total_EUR", "Realized_Balance", "Max_Price_In_Trade"])
            
            current_realized = TOTAL_BUDGET + state.data['total_realized_profit']
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                pair, action, price, amount, total, current_realized, max_price_trade
            ])
    except Exception as e:
        write_daily_log(f"[ERROR] CSV Error: {e}")

def fetch_market_data(pair):
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='5m', limit=100)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        rsi = df.ta.rsi(length=14).iloc[-1]
        current_price = df['close'].iloc[-1]
        previous_price = df['close'].iloc[-2]
        
        price_cache[pair] = current_price
        return current_price, rsi, previous_price
        
    except ccxt.NetworkError:
        write_daily_log(f"[ERROR] Binance Network Error ({pair}): Skipping cycle...")
        return 0, 100, 999999
    except ccxt.ExchangeError:
        write_daily_log(f"[ERROR] Binance Internal Error ({pair}): Skipping cycle...")
        return 0, 100, 999999
    except Exception as e:
        write_daily_log(f"[ERROR] Critical Error fetching data ({pair}): {e}")
        return 0, 100, 999999

# --- MAIN LOOP ---
start_msg = "[INFO] 🤖 BOT V4.0: The Final Boss (Dynamic TTP)"
print(start_msg)
write_daily_log(start_msg)
send_telegram_msg("🚀 **Bot V4.0 Started**\nMode: Ready for Production 🛡️\nEngine: Dynamic Trailing Take Profit Active 📈")

while True:
    try:
        now = datetime.now()
        slots_occupied = state.get_used_slots()
        
        # Daily Report at 10:00 AM
        if now.hour == 10 and now.minute == 0 and state.data['last_report_date'] != now.strftime("%Y-%m-%d"):
            for pair in PARES:
                fetch_market_data(pair)
            
            equity = calculate_total_equity()
            start_bal = state.data['initial_balance']
            yesterday_bal = state.data['yesterday_balance']
            
            daily_pnl = equity - yesterday_bal
            total_pnl = equity - start_bal
            
            report_msg = (
                f"📅 **Daily Summary**\n"
                f"Initial Funds: {start_bal:.2f}€\n"
                f"Current (Floating): {equity:.2f}€\n"
                f"Realized Profit: {'+' if state.data['total_realized_profit']>0 else ''}{state.data['total_realized_profit']:.2f}€\n"
                f"Daily PnL: {'+' if daily_pnl>0 else ''}{daily_pnl:.2f}€\n"
                f"Lifetime PnL: {'+' if total_pnl>0 else ''}{total_pnl:.2f}€"
            )
            send_telegram_msg(report_msg)
            
            state.data['last_report_date'] = now.strftime("%Y-%m-%d")
            state.data['yesterday_balance'] = equity
            state.save()

        log_line = f"[INFO] [{now.strftime('%H:%M')}] "
        extra_logs = []

        for pair in PARES:
            price, rsi, prev_price = fetch_market_data(pair)
            if price == 0: continue
            
            position = state.data['portfolio'][pair]
            bullish = price > prev_price
            trend_icon = "↗️" if bullish else "↘️"
            
            log_line += f"{pair.split('/')[0]}:{price:.1f}€({rsi:.0f}){trend_icon} | "

            # Update position peak price
            if position['amount'] > 0:
                if price > position['max_price_reached']:
                    position['max_price_reached'] = price
                    state.save()

            # --- TTP SELL LOGIC (SHIELDED) ---
            if position['amount'] > 0:
                min_target = position['avg_price'] * (1 + PROFIT_MARGIN)
                peak_price = position['max_price_reached']
                
                # Check if
