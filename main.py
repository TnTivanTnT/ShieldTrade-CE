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

# --- CONFIGURACIÓN MAESTRA V5.1 ---
SIMULATION = True         # True: Dinero ficticio | False: Operativa Real en Binance
TOTAL_BUDGET = 65.80       # Capital base de control
BASE_SLOT_USDC = 14.40     # Tamaño de cada slot inicial
MAX_SLOTS_LIMIT = 6        # Máximo de slots permitidos

PAIRS = ['SOL/USDC', 'ETH/USDC']
MAX_RSI_ENTRY = 45
DCA_DROP_PERCENT = 0.03
PROFIT_MARGIN = 0.015
TRAILING_GAP = 0.003
EMA_MACRO_PERIOD = 200
EMA_MACRO_TIMEFRAME = '1h'

STATE_FILE = "/app/bot_state.json"
CSV_FILE = "/app/trading_history_usdc.csv"
LOGS_FOLDER = "/app/logs"

# --- INICIALIZACIÓN ---
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
        return ticker['last'], df.ta.rsi(length=14).iloc[-1], df['close'].iloc[-2]
    except: return 0, 100, 0

def verify_macro_trend(pair):
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe=EMA_MACRO_TIMEFRAME, limit=250)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        return df.ta.ema(length=EMA_MACRO_PERIOD).iloc[-1]
    except: return float('inf')

def sync_balance(price_cache):
    if SIMULATION:
        invested = sum(d['invested'] for d in state.data['portfolio'].values())
        free_usdc = (state.data['initial_balance'] + state.data['realized_profit']) - invested
        state.data['free_usdc_real'] = free_usdc
    else:
        try:
            bal = exchange.fetch_balance()
            state.data['free_usdc_real'] = bal['USDC']['free']
        except: pass
    
    coin_val = sum(info['amount'] * price_cache.get(p, {}).get('price', 0) for p, info in state.data['portfolio'].items())
    return state.data['free_usdc_real'] + coin_val

# --- BUCLE PRINCIPAL ---
last_log_minute = -1
send_telegram(f"🚀 **ShieldTrade V5.1 Online**\nMode: {'SIMULATION' if SIMULATION else 'REAL LIVE'}")

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
            price, rsi, prev_price = get_market_data(pair)
            if price == 0: continue
            price_cache[pair] = {"price": price, "rsi": round(rsi, 2)}
            pos = state.data['portfolio'][pair]
            log_line += f"{pair.split('/')[0]}:{price:.2f}$ | "

            # --- LÓGICA DE VENTA (Trailing Take Profit) ---
            if pos['amount'] > 0:
                if price > pos['max_price_reached']: 
                    pos['max_price_reached'] = price
                
                target = pos['avg_price'] * (1 + PROFIT_MARGIN)
                if price >= target and price <= (pos['max_price_reached'] * (1 - TRAILING_GAP)):
                    sell_amt = pos['amount']
                    profit = 0
                    
                    if not SIMULATION:
                        bal = exchange.fetch_balance()
                        sell_amt = float(exchange.amount_to_precision(pair, bal[pair.split('/')[0]]['free']))
                        order = exchange.create_market_sell_order(pair, sell_amt)
                        profit = float(order['cost']) - pos['invested']
                    else:
                        profit = (price * sell_amt) - pos['invested']
                    
                    state.data['realized_profit'] += profit
                    with open(CSV_FILE, 'a', newline='') as f:
                        csv.writer(f).writerow([now.strftime('%Y-%m-%d %H:%M:%S'), pair, 'SELL', price, sell_amt, profit])
                    
                    msg = f"🟢 VENTA {pair} | +{profit:.2f}$"
                    send_telegram(msg); log_daily(msg)
                    pos.update({"amount": 0.0, "invested": 0.0, "avg_price": 0.0, "max_price_reached": 0.0})
                    state.save()

            # --- LÓGICA DE COMPRA / DCA ---
            invested_total = sum(d['invested'] for d in state.data['portfolio'].values())
            used_slots = int(round(invested_total / current_slot_size)) if current_slot_size > 0 else 0
            
            if used_slots < current_max_slots:
                is_dca = pos['amount'] > 0 and price < (pos['avg_price'] * (1 - DCA_DROP_PERCENT))
                is_new = pos['amount'] == 0 and rsi < MAX_RSI_ENTRY and price > prev_price
                
                if (is_new or is_dca) and price > verify_macro_trend(pair):
                    buy_qty = current_slot_size / price
                    cost = current_slot_size
                    
                    if not SIMULATION:
                        order = exchange.create_market_buy_order(pair, float(exchange.amount_to_precision(pair, buy_qty)))
                        buy_qty = float(order['filled'])
                        cost = float(order['cost'])
                    
                    pos['invested'] += cost
                    pos['amount'] += buy_qty
                    pos['avg_price'] = pos['invested'] / pos['amount']
                    pos['max_price_reached'] = price
                    
                    with open(CSV_FILE, 'a', newline='') as f:
                        csv.writer(f).writerow([now.strftime('%Y-%m-%d %H:%M:%S'), pair, 'BUY', price, buy_qty, cost])
                    
                    msg = f"🔵 {'DCA' if is_dca else 'BUY'} {pair} | Slot: {current_slot_size}$"
                    send_telegram(msg); log_daily(msg); state.save()

        # Reportes y Sincronización de Saldo para la Web
        equity = sync_balance(price_cache)
        
        if now.hour >= 10 and state.data.get('last_report_date') != now.strftime("%Y-%m-%d"):
            yesterday = state.data.get('yesterday_balance', equity)
            diff = equity - yesterday
            send_telegram(f"📅 **Daily Report V5.1**\nEquity: {equity:.2f}$\nToday: {diff:+.2f}$\nSlots: {current_max_slots}")
            state.data['last_report_date'] = now.strftime("%Y-%m-%d")
            state.data['yesterday_balance'] = equity
            state.save()

        if now.minute == 0 and state.data.get('last_db_snapshot_hour') != now.hour:
            database.save_snapshot(equity, price_cache.get('SOL/USDC', {}).get('price', 0), price_cache.get('ETH/USDC', {}).get('price', 0))
            state.data['last_db_snapshot_hour'] = now.hour; state.save()

        if now.minute != last_log_minute:
            print(log_line); log_daily(log_line); last_log_minute = now.minute
        
        state.save(live_prices=price_cache)
        time.sleep(10)
    except Exception as e: 
        print(f"Error: {e}"); time.sleep(30)
