import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import time
import json
import os
import csv
from datetime import datetime, timedelta
from dotenv import load_dotenv
import database

load_dotenv("/app/.env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

# --- CONFIGURACIÓN MAESTRA V5.1 ---
# TOTAL_BUDGET ahora solo sirve para el cálculo del % de beneficio en la web
TOTAL_BUDGET = 65.80      
BASE_SLOT_USDC = 14.40     
MAX_SLOTS_LIMIT = 6

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

database.init_db()
exchange = ccxt.binance({
    'enableRateLimit': True,
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'options': {'defaultType': 'spot'}
})

# ... (funciones log_daily, send_telegram, verify_macro_trend se mantienen igual) ...

def get_market_data(pair):
    try:
        ticker = exchange.fetch_ticker(pair)
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='5m', limit=50)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        return ticker['last'], df.ta.rsi(length=14).iloc[-1], df['close'].iloc[-2]
    except: return 0, 100, 0

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

def calculate_real_total_equity(price_cache):
    try:
        bal = exchange.fetch_balance()
        free_usdc = bal['USDC']['free']
        state.data['free_usdc_real'] = free_usdc
        coin_val = sum(info['amount'] * price_cache.get(p, {}).get('price', 0) for p, info in state.data['portfolio'].items())
        return free_usdc + coin_val
    except:
        return state.data.get('initial_balance', 65.80) + state.data.get('realized_profit', 0)

# --- INICIO ---
last_log_minute = -1
send_telegram(f"🚀 **ShieldTrade V5.1 Full-Auto Online**")

while True:
    try:
        now = datetime.now()
        price_cache = {}
        
        # 1. Primero obtenemos precios para saber cuánto valen nuestras monedas
        for pair in PAIRS:
            price, rsi, prev_price = get_market_data(pair)
            if price > 0:
                price_cache[pair] = {"price": price, "rsi": round(rsi, 2), "prev": prev_price}

        # 2. CALCULAMOS EL CAPITAL REAL (USDC Libre + Valor de Criptos)
        # Esto es lo que permite que si metes 20€ el bot se entere al instante
        real_equity = calculate_real_total_equity(price_cache)
        
        # 3. Escalado dinámico basado en el CAPITAL REAL de Binance
        if real_equity < (BASE_SLOT_USDC * MAX_SLOTS_LIMIT):
            current_max_slots = int(real_equity // BASE_SLOT_USDC)
            current_slot_size = BASE_SLOT_USDC
        else:
            current_max_slots = MAX_SLOTS_LIMIT
            current_slot_size = round(real_equity / MAX_SLOTS_LIMIT, 2)

        log_line = f"[INFO] [{now.strftime('%H:%M:%S')}] RealCap:{real_equity:.2f}$ Slot:{current_slot_size}$ | "
        
        for pair in PAIRS:
            if pair not in price_cache: continue
            price = price_cache[pair]['price']
            rsi = price_cache[pair]['rsi']
            prev_price = price_cache[pair]['prev']
            pos = state.data['portfolio'][pair]
            log_line += f"{pair.split('/')[0]}:{price:.2f}$ | "

            # VENTA (ANTI-DUST)
            if pos['amount'] > 0:
                if price > pos['max_price_reached']: 
                    pos['max_price_reached'] = price
                
                target = pos['avg_price'] * (1 + PROFIT_MARGIN)
                if price >= target and price <= (pos['max_price_reached'] * (1 - TRAILING_GAP)):
                    # Barrido total
                    bal = exchange.fetch_balance()
                    sell_amt = float(exchange.amount_to_precision(pair, bal[pair.split('/')[0]]['free']))
                    
                    if sell_amt > 0:
                        order = exchange.create_market_sell_order(pair, sell_amt)
                        profit = float(order['cost']) - pos['invested']
                        state.data['realized_profit'] += profit
                        
                        with open(CSV_FILE, 'a', newline='') as f:
                            csv.writer(f).writerow([now.strftime('%Y-%m-%d %H:%M:%S'), pair, 'SELL', price, sell_amt, profit])
                        
                        send_telegram(f"🟢 VENTA {pair} | +{profit:.2f}$")
                        pos.update({"amount": 0.0, "invested": 0.0, "avg_price": 0.0, "max_price_reached": 0.0})

            # COMPRA / DCA (Basado en el nuevo capital detectado)
            invested_total = sum(d['invested'] for d in state.data['portfolio'].values())
            used_slots = int(round(invested_total / current_slot_size)) if current_slot_size > 0 else 0
            
            if used_slots < current_max_slots:
                is_dca = pos['amount'] > 0 and price < (pos['avg_price'] * (1 - DCA_DROP_PERCENT))
                is_new = pos['amount'] == 0 and rsi < MAX_RSI_ENTRY and price > prev_price
                
                if (is_new or is_dca) and price > verify_macro_trend(pair):
                    buy_amt = float(exchange.amount_to_precision(pair, current_slot_size/price))
                    order = exchange.create_market_buy_order(pair, buy_amt)
                    pos['invested'] += float(order['cost'])
                    pos['amount'] += float(order['filled'])
                    pos['avg_price'] = pos['invested'] / pos['amount']
                    pos['max_price_reached'] = price
                    
                    with open(CSV_FILE, 'a', newline='') as f:
                        csv.writer(f).writerow([now.strftime('%Y-%m-%d %H:%M:%S'), pair, 'BUY', price, order['filled'], order['cost']])
                    
                    send_telegram(f"🔵 COMPRA {pair} | Slot: {current_slot_size}$")

        # REPORTE Y SNAPSHOTS (Se mantienen igual)
        # ... 

        state.save(live_prices=price_cache)
        if now.minute != last_log_minute:
            print(log_line); last_log_minute = now.minute
        time.sleep(10)
    except Exception as e: print(f"Error: {e}"); time.sleep(30)