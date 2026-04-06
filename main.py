import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import time
import json
import os
import csv
from datetime import datetime
from dotenv import load_dotenv
import database

load_dotenv("/app/.env")

# --- CREDENCIALES ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

# --- CONFIGURACIÓN MAESTRA V5.2 ---
SIMULATION = True
TOTAL_BUDGET = 60.72      # Tu capital real tras la venta
BASE_SLOT_USDC = 14.40     
MAX_SLOTS_LIMIT = 6        

PAIRS = ['SOL/USDC', 'ETH/USDC']
Z_SCORE_ENTRY = -2.0     
DCA_DROP_PERCENT = 0.03
PROFIT_MARGIN = 0.015
TRAILING_GAP = 0.003
EMA_MACRO_PERIOD = 200
EMA_MACRO_TIMEFRAME = '1h'

STATE_FILE = "/app/bot_state.json"
CSV_FILE = "/app/trading_history_usdc.csv"
LOGS_FOLDER = "/app/logs"

database.init_db()
exchange = ccxt.binance({
    'enableRateLimit': True,
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'options': {'defaultType': 'spot'}
})

def send_telegram(message):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except: pass

def log_daily(message):
    try:
        os.makedirs(LOGS_FOLDER, exist_ok=True)
        filename = f"{LOGS_FOLDER}/log_{datetime.now().strftime('%Y-%m-%d')}.txt"
        with open(filename, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
    except: pass

class StateManager:
    def __init__(self):
        self.data = {}
        self.load()
        if self.data.get('initial_balance') != TOTAL_BUDGET:
            self.data['initial_balance'] = TOTAL_BUDGET
            self.save()

    def load(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f: self.data = json.load(f)

    def save(self, live_prices=None):
        if live_prices: self.data["current_market_prices"] = live_prices
        with open(STATE_FILE + ".tmp", 'w') as f: json.dump(self.data, f, indent=4)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)

state = StateManager()

def get_market_data(pair):
    try:
        ticker = exchange.fetch_ticker(pair)
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='5m', limit=50)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # --- CÁLCULO Z-SCORE V5.2 ---
        df['sma'] = df['close'].rolling(window=20).mean()
        df['std'] = df['close'].rolling(window=20).std()
        current_close = df['close'].iloc[-1]
        last_sma = df['sma'].iloc[-1]
        last_std = df['std'].iloc[-1]
        
        z_score = (current_close - last_sma) / last_std if last_std > 0 else 0
        return ticker['last'], round(z_score, 2), df['close'].iloc[-2]
    except: return 0, 0, 0

def verify_macro_trend(pair):
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe=EMA_MACRO_TIMEFRAME, limit=250)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        return df.ta.ema(length=EMA_MACRO_PERIOD).iloc[-1]
    except: return float('inf')

def sync_balance(price_cache):
    try:
        bal = exchange.fetch_balance()
        state.data['free_usdc_real'] = bal['USDC']['free']
    except: pass
    coin_val = sum(info['amount'] * price_cache.get(p, {}).get('price', 0) for p, info in state.data['portfolio'].items())
    return state.data['free_usdc_real'] + coin_val

# --- BUCLE PRINCIPAL ---
last_log_minute = -1
send_telegram(f"🛡️ **ShieldTrade V5.2 Simons Edition**\nZ-Score Activo: {Z_SCORE_ENTRY} σ")

while True:
    try:
        now = datetime.now()
        total_cap = state.data['initial_balance'] + state.data['realized_profit']
        
        # Escalado Fluido
        if total_cap < (BASE_SLOT_USDC * MAX_SLOTS_LIMIT):
            current_max_slots = int(total_cap // BASE_SLOT_USDC)
            current_slot_size = BASE_SLOT_USDC
        else:
            current_max_slots = MAX_SLOTS_LIMIT
            current_slot_size = round(total_cap / MAX_SLOTS_LIMIT, 2)

        price_cache = {}
        log_line = f"Cap:{total_cap:.2f}$ | Slot:{current_slot_size}$ | "
        
        for pair in PAIRS:
            price, z_score, prev_price = get_market_data(pair)
            if price == 0: continue
            price_cache[pair] = {"price": price, "z_score": z_score}
            pos = state.data['portfolio'][pair]
            log_line += f"{pair.split('/')[0]}:{z_score}σ | "

            # VENTA (TTP)
            if pos['amount'] > 0:
                if price > pos['max_price_reached']: pos['max_price_reached'] = price
                target = pos['avg_price'] * (1 + PROFIT_MARGIN)
                if price >= target and price <= (pos['max_price_reached'] * (1 - TRAILING_GAP)):
                    bal = exchange.fetch_balance()
                    sell_amt = float(exchange.amount_to_precision(pair, bal[pair.split('/')[0]]['free']))
                    order = exchange.create_market_sell_order(pair, sell_amt)
                    profit = float(order['cost']) - pos['invested']
                    state.data['realized_profit'] += profit
                    with open(CSV_FILE, 'a', newline='') as f:
                        csv.writer(f).writerow([now.strftime('%Y-%m-%d %H:%M:%S'), pair, 'SELL', price, sell_amt, profit])
                    send_telegram(f"🟢 VENTA {pair} | +{profit:.2f}$"); log_daily(f"SELL {pair} +{profit:.2f}$")
                    pos.update({"amount": 0.0, "invested": 0.0, "avg_price": 0.0, "max_price_reached": 0.0})
                    state.save()

            # COMPRA / DCA (Lógica Z-Score)
            invested_total = sum(d['invested'] for d in state.data['portfolio'].values())
            used_slots = int(round(invested_total / current_slot_size)) if current_slot_size > 0 else 0
            
            if used_slots < current_max_slots:
                is_dca = pos['amount'] > 0 and price < (pos['avg_price'] * (1 - DCA_DROP_PERCENT))
                is_new = pos['amount'] == 0 and z_score < Z_SCORE_ENTRY and price > prev_price
                
                if (is_new or is_dca) and price > verify_macro_trend(pair):
                    buy_qty = float(exchange.amount_to_precision(pair, current_slot_size/price))
                    order = exchange.create_market_buy_order(pair, buy_qty)
                    pos['invested'] += float(order['cost'])
                    pos['amount'] += float(order['filled'])
                    pos['avg_price'] = pos['invested'] / pos['amount']
                    pos['max_price_reached'] = price
                    with open(CSV_FILE, 'a', newline='') as f:
                        csv.writer(f).writerow([now.strftime('%Y-%m-%d %H:%M:%S'), pair, 'BUY', price, order['filled'], order['cost']])
                    send_telegram(f"🔵 {'DCA' if is_dca else 'BUY'} {pair} | Z:{z_score}σ"); state.save()

        equity = sync_balance(price_cache)
        # Reporte Diario
        if now.hour >= 10 and state.data.get('last_report_date') != now.strftime("%Y-%m-%d"):
            yesterday = state.data.get('yesterday_balance', equity)
            send_telegram(f"📅 **Daily Report V5.2**\nEquity: {equity:.2f}$\nProfit: {equity-yesterday:+.2f}$")
            state.data['last_report_date'] = now.strftime("%Y-%m-%d")
            state.data['yesterday_balance'] = equity; state.save()

        if now.minute == 0 and state.data.get('last_db_snapshot_hour') != now.hour:
            database.save_snapshot(equity, price_cache.get('SOL/USDC', {}).get('price', 0), price_cache.get('ETH/USDC', {}).get('price', 0))
            state.data['last_db_snapshot_hour'] = now.hour; state.save()

        if now.minute != last_log_minute:
            print(log_line); log_daily(log_line); last_log_minute = now.minute
        
        state.save(live_prices=price_cache)
        time.sleep(10)
    except Exception as e: print(f"Error: {e}"); time.sleep(30)
