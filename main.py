import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import time
import json
import os
import shutil  # <-- LIBRERÍA NUEVA PARA COPIAR ARCHIVOS
import csv
import threading
from datetime import datetime
from dotenv import load_dotenv, set_key
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# --- CONFIGURACIÓN EN RAM ---
class NodeConfig:
    is_sim = True
    api_key = ""
    secret_key = ""
    total_budget_real = 0.0
    total_budget_sim = 0.0

env_path = "/app/.env"
example_env_path = "/app/.env.example"

# --- AUTOCREACIÓN DEL .ENV ---
# Si el usuario no ha creado el .env a mano, el bot lo hace por él
if not os.path.exists(env_path):
    if os.path.exists(example_env_path):
        shutil.copy(example_env_path, env_path)
        print("📁 Archivo .env autogenerado desde .env.example")
    else:
        # Si por algún casual tampoco existe el example, crea uno vacío
        open(env_path, 'w').close()
        print("📁 Archivo .env creado desde cero")

load_dotenv(env_path)

NodeConfig.api_key = os.getenv("API_KEY", "")
NodeConfig.secret_key = os.getenv("SECRET_KEY", "")
# ... (sigue el resto de tu código normal)
NodeConfig.total_budget_real = float(os.getenv("TOTAL_BUDGET", "0"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- PARÁMETROS V6.0 ---
BASE_SLOT_USDC = 14.40
MAX_SLOTS_LIMIT = 6
PAIRS = ['SOL/USDC', 'ETH/USDC']
Z_SCORE_ENTRY = -2.0
DCA_DROP_PERCENT = 0.03
PROFIT_MARGIN = 0.015
ATR_MULTIPLIER = 1.5  
KELLY_FRACTION_MAX = 0.20 

exchange = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
if NodeConfig.api_key:
    exchange.apiKey = NodeConfig.api_key
    exchange.secret = NodeConfig.secret_key

app = FastAPI()

# --- ENDPOINTS FASTAPI (Actualizado para Telegram) ---
class ConfigPayload(BaseModel):
    mode: str
    sim_budget: float = None
    api_key: str = None
    secret_key: str = None
    real_budget: float = None
    telegram_token: str = None
    telegram_chat_id: str = None

@app.post("/api/config")
def update_config(payload: ConfigPayload):
    global TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
    
    if payload.mode == "init_sim" and payload.sim_budget is not None:
        NodeConfig.total_budget_sim = payload.sim_budget
        state.data['initial_balance'] = payload.sim_budget
        state.data['free_usdc_real'] = payload.sim_budget
        state.save()
        return {"status": "success", "mode": "sim"}
        
    elif payload.mode == "real":
        # Guardar en memoria y escribir en el archivo .env físico
        if payload.api_key:
            NodeConfig.api_key = payload.api_key
            set_key(env_path, "API_KEY", payload.api_key)
            exchange.apiKey = payload.api_key
        if payload.secret_key:
            NodeConfig.secret_key = payload.secret_key
            set_key(env_path, "SECRET_KEY", payload.secret_key)
            exchange.secret = payload.secret_key
        if payload.real_budget:
            NodeConfig.total_budget_real = payload.real_budget
            set_key(env_path, "TOTAL_BUDGET", str(payload.real_budget))
        
        # Nuevos campos de Telegram
        if payload.telegram_token:
            TELEGRAM_TOKEN = payload.telegram_token
            set_key(env_path, "TELEGRAM_TOKEN", payload.telegram_token)
        if payload.telegram_chat_id:
            TELEGRAM_CHAT_ID = payload.telegram_chat_id
            set_key(env_path, "TELEGRAM_CHAT_ID", payload.telegram_chat_id)

        NodeConfig.is_sim = False
        state.load()
        return {"status": "success", "mode": "real"}
        
    elif payload.mode == "simulation":
        NodeConfig.is_sim = True
        state.load()
        return {"status": "success", "mode": "simulation"}

@app.get("/status")
def get_status():
    state.load()
    return {
        "is_sim": NodeConfig.is_sim,
        "has_api": bool(NodeConfig.api_key),
        "needs_sim_setup": NodeConfig.is_sim and state.data.get("initial_balance", 0.0) == 0.0,
        "data": state.data
    }

@app.get("/", response_class=HTMLResponse)
def read_root():
    with open("/app/static/index.html", "r", encoding="utf-8") as f:
        return f.read()

def send_telegram(message):
    if not TELEGRAM_TOKEN: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except: pass

# --- STATE MANAGER ---
class StateManager:
    def __init__(self):
        self.data = {}
    
    def get_file(self):
        return "/app/data/bot_state_sim.json" if NodeConfig.is_sim else "/app/data/bot_state.json"

    def load(self):
        file = self.get_file()
        if os.path.exists(file):
            with open(file, 'r') as f: 
                self.data = json.load(f)
                if "metrics" not in self.data:
                    self.data["metrics"] = {"wins": 0, "losses": 0, "total_win_pct": 0.0, "total_loss_pct": 0.0}
        else:
            self.data = {
                "initial_balance": 0.0,
                "realized_profit": 0.0,
                "portfolio": {p: {"amount": 0.0, "invested": 0.0, "avg_price": 0.0, "max_price_reached": 0.0} for p in PAIRS},
                "free_usdc_real": 0.0,
                "metrics": {"wins": 0, "losses": 0, "total_win_pct": 0.0, "total_loss_pct": 0.0}
            }
            self.save()

    def save(self, live_prices=None):
        if live_prices: self.data["current_market_prices"] = live_prices
        file = self.get_file()
        with open(file + ".tmp", 'w') as f: json.dump(self.data, f, indent=4)
        os.replace(file + ".tmp", file)

state = StateManager()

# --- MOTORES DE ANÁLISIS ---
def get_market_data(pair):
    try:
        ticker = exchange.fetch_ticker(pair)
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='5m', limit=50)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df['sma'] = df['close'].rolling(window=20).mean()
        df['std'] = df['close'].rolling(window=20).std()
        std_val = df['std'].iloc[-1]
        z_score = (df['close'].iloc[-1] - df['sma'].iloc[-1]) / std_val if std_val > 0 else 0
        
        df.ta.atr(length=14, append=True)
        atr_val = df['ATRr_14'].iloc[-1]
        current_price = ticker['last']
        atr_pct = atr_val / current_price if current_price > 0 else 0.003
        
        return current_price, round(z_score, 2), atr_pct
    except Exception as e: 
        print(f"Error Market Data {pair}: {e}")
        return 0, 0, 0.003

def get_btc_trend():
    try:
        ohlcv = exchange.fetch_ohlcv('BTC/USDC', timeframe='1h', limit=30)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['sma'] = df['close'].rolling(window=20).mean()
        return df['close'].iloc[-1] > df['sma'].iloc[-1]
    except:
        return True

def calculate_kelly_slot(total_cap):
    metrics = state.data['metrics']
    total_trades = metrics['wins'] + metrics['losses']
    
    if total_trades < 5:
        return BASE_SLOT_USDC
    
    W = metrics['wins'] / total_trades
    avg_win = metrics['total_win_pct'] / metrics['wins'] if metrics['wins'] > 0 else 0
    avg_loss = metrics['total_loss_pct'] / metrics['losses'] if metrics['losses'] > 0 else 0.01
    R = avg_win / avg_loss
    
    kelly_pct = W - ((1 - W) / R)
    half_kelly = kelly_pct / 2
    
    safe_kelly = max(0.01, min(half_kelly, KELLY_FRACTION_MAX))
    return round(total_cap * safe_kelly, 2)

def sync_balance(price_cache):
    if NodeConfig.is_sim:
        invested = sum(d['invested'] for d in state.data['portfolio'].values())
        free_usdc = (state.data['initial_balance'] + state.data['realized_profit']) - invested
        state.data['free_usdc_real'] = free_usdc
    else:
        try:
            bal = exchange.fetch_balance()
            state.data['free_usdc_real'] = bal['USDC']['free']
        except: pass

# --- NODO CENTRAL DE TRADING ---
def trading_node_loop():
    print("🚀 Motor Híbrido V6.0 iniciado (ATR + Kelly + BTC Filter)...")
    last_report_day = None

    while True:
        try:
            state.load()
            if state.data.get('initial_balance', 0) == 0:
                time.sleep(5)
                continue

            total_cap = state.data['initial_balance'] + state.data['realized_profit']
            current_slot_size = calculate_kelly_slot(total_cap)
            current_max_slots = MAX_SLOTS_LIMIT
            
            is_btc_bullish = get_btc_trend()
            price_cache = {}
            
            for pair in PAIRS:
                price, z_score, atr_pct = get_market_data(pair)
                if price == 0: continue
                
                dynamic_trailing_gap = max(0.002, atr_pct * ATR_MULTIPLIER)
                price_cache[pair] = {"price": price, "z_score": z_score, "atr_gap": dynamic_trailing_gap}
                pos = state.data['portfolio'][pair]

                if pos['amount'] > 0:
                    if price > pos['max_price_reached']: pos['max_price_reached'] = price
                    target = pos['avg_price'] * (1 + PROFIT_MARGIN)
                    
                    if price >= target and price <= (pos['max_price_reached'] * (1 - dynamic_trailing_gap)):
                        if NodeConfig.is_sim:
                            profit = (price * pos['amount']) - pos['invested']
                            sell_amt = pos['amount']
                        else:
                            bal = exchange.fetch_balance()
                            sell_amt = float(exchange.amount_to_precision(pair, bal[pair.split('/')[0]]['free']))
                            order = exchange.create_market_sell_order(pair, sell_amt)
                            profit = float(order['cost']) - pos['invested']
                        
                        pct_gain = profit / pos['invested']
                        if profit > 0:
                            state.data['metrics']['wins'] += 1
                            state.data['metrics']['total_win_pct'] += pct_gain
                        else:
                            state.data['metrics']['losses'] += 1
                            state.data['metrics']['total_loss_pct'] += abs(pct_gain)

                        state.data['realized_profit'] += profit
                        pos.update({"amount": 0.0, "invested": 0.0, "avg_price": 0.0, "max_price_reached": 0.0})
                        
                        csv_file = "/app/data/trading_history_sim.csv" if NodeConfig.is_sim else "/app/data/trading_history_usdc.csv"
                        with open(csv_file, 'a', newline='') as f:
                            csv.writer(f).writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'), pair, 'SELL', price, sell_amt, profit])
                        
                        mode_str = "[SIM] " if NodeConfig.is_sim else "[REAL] "
                        send_telegram(f"{mode_str}🟢 VENTA {pair} | +{profit:.2f}$ | T-Gap: {dynamic_trailing_gap*100:.2f}%")
                        state.save()

                invested_total = sum(d['invested'] for d in state.data['portfolio'].values())
                used_slots = int(round(invested_total / current_slot_size)) if current_slot_size > 0 else 0
                
                if used_slots < current_max_slots:
                    is_dca = pos['amount'] > 0 and price < (pos['avg_price'] * (1 - DCA_DROP_PERCENT))
                    is_new = pos['amount'] == 0 and z_score < Z_SCORE_ENTRY and is_btc_bullish
                    
                    if is_new or is_dca:
                        if NodeConfig.is_sim:
                            buy_qty = current_slot_size / price
                            cost = current_slot_size
                        else:
                            buy_qty = float(exchange.amount_to_precision(pair, current_slot_size/price))
                            order = exchange.create_market_buy_order(pair, buy_qty)
                            buy_qty = float(order['filled'])
                            cost = float(order['cost'])
                        
                        pos['invested'] += cost
                        pos['amount'] += buy_qty
                        pos['avg_price'] = pos['invested'] / pos['amount']
                        pos['max_price_reached'] = price
                        
                        csv_file = "/app/data/trading_history_sim.csv" if NodeConfig.is_sim else "/app/data/trading_history_usdc.csv"
                        with open(csv_file, 'a', newline='') as f:
                            csv.writer(f).writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S'), pair, 'BUY', price, buy_qty, cost])

                        mode_str = "[SIM] " if NodeConfig.is_sim else "[REAL] "
                        tipo = "DCA" if is_dca else "NUEVA"
                        send_telegram(f"{mode_str}🔵 COMPRA {tipo} {pair} | Z:{z_score}σ | Slot: {cost:.2f}$")
                        state.save()

            sync_balance(price_cache)
            state.save(live_prices=price_cache)
            
            now = datetime.now()
            log_str = f"[{now.strftime('%H:%M:%S')}] Cap:{total_cap:.2f}$ | Slot:{current_slot_size:.2f}$ | "
            for pair in PAIRS:
                z = price_cache.get(pair, {}).get('z_score', 0)
                log_str += f"{pair.split('/')[0]}:{z}σ | "
            print(log_str)
            
            if now.hour == 11 and now.minute == 0 and last_report_day != now.day:
                mode_str = "[SIM] " if NodeConfig.is_sim else "[REAL] "
                send_telegram(f"📅 {mode_str}Daily Report V6.0\nEquity: {total_cap:.2f}$\nProfit Total: {state.data['realized_profit']:.2f}$")
                last_report_day = now.day

            time.sleep(10)
        except Exception as e:
            print(f"Error Loop: {e}")
            time.sleep(10)

threading.Thread(target=trading_node_loop, daemon=True).start()