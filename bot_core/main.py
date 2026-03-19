import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import time
import json
import os
import csv
import glob
import gc
from datetime import datetime, timedelta
from dotenv import load_dotenv
import database

# --- CARGAR CREDENCIALES ---
load_dotenv("/app/.env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, API_KEY, SECRET_KEY]):
    print("[CRÍTICO] Faltan variables en el archivo .env. Bot detenido.")
    exit()

# --- USER CONFIGURATION ---
SIMULATION = True
PAIRS = ['SOL/USDC', 'ETH/USDC']
TOTAL_BUDGET = 59.63
SLOT_SIZE = 14.5
MAX_SLOTS = int(TOTAL_BUDGET // SLOT_SIZE)

# Strategy: DCA + RSI + MACRO + TTP
MAX_RSI_ENTRY = 45
DCA_DROP_PERCENT = 0.03
PROFIT_MARGIN = 0.015
TRAILING_GAP = 0.003
BINANCE_FEE = 0.001
LOG_RETENTION_DAYS = 15

# Macro Filter
EMA_MACRO_PERIOD = 200
EMA_MACRO_TIMEFRAME = '1h'

# --- PATHS ---
CSV_FILE = "/app/trading_history_usdc.csv" 
STATE_FILE = "/app/bot_state.json"
LOGS_FOLDER = "/app/logs"

# --- INITIALIZATION ---
database.init_db()
exchange = ccxt.binance({'enableRateLimit': True})
if not SIMULATION:
    exchange.apiKey = API_KEY
    exchange.secret = SECRET_KEY

def log_daily(message):
    try:
        os.makedirs(LOGS_FOLDER, exist_ok=True)
        filename = f"{LOGS_FOLDER}/log_{datetime.now().strftime('%Y-%m-%d')}.txt"
        with open(filename, "a", encoding="utf-8") as f:
            f.write(f"{message}\n")
            
        cutoff_date = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
        log_files = glob.glob(f"{LOGS_FOLDER}/log_*.txt")
        for file in log_files:
            date_str = file.split('_')[-1].replace('.txt', '')
            try:
                file_date = datetime.strptime(date_str, '%Y-%m-%d')
                if file_date < cutoff_date:
                    os.remove(file)
            except: pass
    except Exception as e: 
        print(f"[ERROR] Logging system failed: {e}")

try:
    exchange.load_markets()
except Exception as e:
    msg = f"[ERROR] Failed to load markets initially: {e}"
    print(msg)
    log_daily(msg)

price_cache = {pair: 0.0 for pair in PAIRS}

# --- STATE MANAGER ---
class StateManager:
    def __init__(self):
        self.data = {
            "initial_balance": TOTAL_BUDGET,
            "yesterday_balance": TOTAL_BUDGET,
            "last_report_date": "",
            "last_db_snapshot_hour": -1,
            "realized_profit": 0.0,
            "portfolio": {pair: {"amount": 0.0, "avg_price": 0.0, "invested": 0.0, "max_price_reached": 0.0} for pair in PAIRS},
            "current_market_prices": {} # Variable inyectada para la App
        }
        self.load()
        self.reconcile_balances()

    def load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    loaded_data = json.load(f)
                    self.data["initial_balance"] = loaded_data.get("initial_balance", TOTAL_BUDGET)
                    self.data["yesterday_balance"] = loaded_data.get("yesterday_balance", TOTAL_BUDGET)
                    self.data["last_report_date"] = loaded_data.get("last_report_date", "")
                    self.data["last_db_snapshot_hour"] = loaded_data.get("last_db_snapshot_hour", -1)
                    self.data["realized_profit"] = loaded_data.get("realized_profit", 0.0)
                    self.data["current_market_prices"] = loaded_data.get("current_market_prices", {})
                    
                    if "portfolio" in loaded_data:
                        for pair, info in loaded_data["portfolio"].items():
                            if pair in self.data["portfolio"]:
                                self.data["portfolio"][pair]["amount"] = info.get("amount", 0.0)
                                self.data["portfolio"][pair]["avg_price"] = info.get("avg_price", 0.0)
                                self.data["portfolio"][pair]["invested"] = info.get("invested", 0.0)
                                self.data["portfolio"][pair]["max_price_reached"] = info.get("max_price_reached", 0.0)
            except:
                msg = "[ERROR] JSON Error, starting from scratch."
                print(msg)
                log_daily(msg)

    def save(self, live_prices=None):
        if live_prices is not None:
            self.data["current_market_prices"] = live_prices
            
        tmp_file = f"{STATE_FILE}.tmp"
        try:
            with open(tmp_file, 'w') as f:
                json.dump(self.data, f, indent=4)
            os.replace(tmp_file, STATE_FILE)
        except Exception as e:
            msg = f"[ERROR] Failed to save JSON: {e}"
            print(msg)
            log_daily(msg)

    def reconcile_balances(self):
        if SIMULATION: return
        try:
            real_balances = exchange.fetch_balance()
            changed = False
            for pair, info in self.data['portfolio'].items():
                base_coin = pair.split('/')[0]
                real_amount = real_balances.get(base_coin, {}).get('free', 0.0)
                json_amount = info['amount']

                if real_amount == 0 and json_amount > 0:
                    log_daily(f"[WARNING] {base_coin} is 0 in Binance but > 0 in JSON. Resetting slot.")
                    info['amount'] = 0.0
                    info['invested'] = 0.0
                    info['avg_price'] = 0.0
                    info['max_price_reached'] = 0.0
                    changed = True
                elif real_amount > 0 and json_amount == 0:
                    log_daily(f"[WARNING] Found {real_amount} {base_coin} in Binance not tracked in JSON. Ignoring to protect DCA math.")
            
            if changed: self.save()
        except ccxt.ExchangeError as e:
            err_str = str(e)
            if '-2015' in err_str or 'Invalid API-key, IP, or permissions' in err_str:
                send_telegram("🚨 **ERROR DE SEGURIDAD**: Binance ha rechazado la IP actual. Actualiza la IP en el panel de Binance.")
                log_daily("[ERROR] IP Rechazada por Binance (-2015) durante reconciliación.")
        except Exception as e:
            log_daily(f"[ERROR] Auto-reconciliation failed: {e}")

    def get_used_slots(self):
        total_invested = sum(d['invested'] for d in self.data['portfolio'].values())
        return int(total_invested // SLOT_SIZE)

state = StateManager()

# --- MARKET FUNCTIONS ---
def send_telegram(message):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except: pass

def calculate_total_equity():
    free_cash = (TOTAL_BUDGET + state.data['realized_profit']) - sum(d['invested'] for d in state.data['portfolio'].values())
    coin_value = sum(info['amount'] * price_cache.get(pair, 0.0) for pair, info in state.data['portfolio'].items() if info['amount'] > 0)
    return free_cash + coin_value

def save_history(action, pair, price, amount, total, max_trade_price=0.0):
    try:
        file_exists = os.path.exists(CSV_FILE)
        with open(CSV_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Date", "Pair", "Action", "Price", "Amount", "Total_USDC", "Realized_Balance", "Max_Trade_Price"])
            
            current_realized = TOTAL_BUDGET + state.data['realized_profit']
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                pair, action, price, amount, total, current_realized, max_trade_price
            ])
    except Exception as e:
        log_daily(f"[ERROR] CSV Error: {e}")

def get_market_data(pair):
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='5m', limit=100)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        rsi = df.ta.rsi(length=14).iloc[-1]
        current_price = df['close'].iloc[-1]
        previous_price = df['close'].iloc[-2]
        
        price_cache[pair] = current_price
        return current_price, rsi, previous_price
        
    except ccxt.ExchangeError as e:
        err_str = str(e)
        if '-2015' in err_str or 'Invalid API-key, IP, or permissions' in err_str:
            log_daily(f"[ERROR] IP Rechazada por Binance (-2015) obteniendo datos de {pair}.")
            send_telegram("🚨 **ERROR DE SEGURIDAD**: Binance ha rechazado la IP actual. Actualiza la IP en el panel de Binance.")
        else:
            log_daily(f"[ERROR] Binance Exchange Error ({pair}): {e}")
        return 0, 100, 999999
    except ccxt.NetworkError:
        log_daily(f"[ERROR] Binance Network Error ({pair}): Skipping cycle...")
        return 0, 100, 999999
    except Exception as e:
        log_daily(f"[ERROR] Critical fetch error ({pair}): {e}")
        return 0, 100, 999999

def verify_macro_trend(pair):
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe=EMA_MACRO_TIMEFRAME, limit=250)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        ema_200 = df.ta.ema(length=EMA_MACRO_PERIOD).iloc[-1]
        
        del df
        del ohlcv
        gc.collect()
        
        return ema_200
    except Exception as e:
        log_daily(f"[ERROR] EMA 200 Macro fetch failed for {pair}: {e}")
        return float('inf')

# --- MAIN LOOP ---
start_msg = "[INFO] 🤖 BOT V4.4: App Backend & Env Security"
print(start_msg)
log_daily(start_msg)
send_telegram("🚀 **Bot V4.4 Iniciado**\nBackend: DB & API Preparadas 🛡️\nMotor: TTP Dinámico + EMA200 1H")

while True:
    try:
        now = datetime.now()
        used_slots = state.get_used_slots()
        today_str = now.strftime("%Y-%m-%d")
        
        # Snapshot SQLite Horario
        if now.minute == 0 and state.data.get('last_db_snapshot_hour') != now.hour:
            equity = calculate_total_equity()
            sol_px = price_cache.get('SOL/USDC', 0.0)
            eth_px = price_cache.get('ETH/USDC', 0.0)
            
            # Solo guardamos si ya tenemos datos reales
            if sol_px > 0 and eth_px > 0:
                database.save_snapshot(equity, sol_px, eth_px)
                state.data['last_db_snapshot_hour'] = now.hour
                state.save()
        
        # Reporte Diario Telegram
        if now.hour >= 10 and state.data['last_report_date'] != today_str:
            for pair in PAIRS:
                get_market_data(pair)
            
            equity = calculate_total_equity()
            init_bal = state.data['initial_balance']
            yest_bal = state.data['yesterday_balance']
            
            daily_diff = equity - yest_bal
            hist_diff = equity - init_bal
            
            resumen_msg = (
                f"📅 **Resumen Diario**\n"
                f"Fondo inicial: {init_bal:.2f}$\n"
                f"Actual (Flotante): {equity:.2f}$\n"
                f"Beneficio Realizado: {'+' if state.data['realized_profit']>0 else ''}{state.data['realized_profit']:.2f}$\n"
                f"Balance diario: {'+' if daily_diff>0 else ''}{daily_diff:.2f}$\n"
                f"Balance histórico: {'+' if hist_diff>0 else ''}{hist_diff:.2f}$"
            )
            send_telegram(resumen_msg)
            
            state.data['last_report_date'] = today_str
            state.data['yesterday_balance'] = equity
            state.save()

        log_line = f"[INFO] [{now.strftime('%H:%M')}] "
        extra_logs = []

        for pair in PAIRS:
            price, rsi, prev_price = get_market_data(pair)
            if price == 0: continue
            
            pos = state.data['portfolio'][pair]
            is_uptrend = price > prev_price
            trend_icon = "↗️" if is_uptrend else "↘️"
            
            log_line += f"{pair.split('/')[0]}:{price:.1f}$({rsi:.0f}){trend_icon} | "

            if pos['amount'] > 0:
                if price > pos['max_price_reached']:
                    pos['max_price_reached'] = price
                    state.save()

            # --- TTP SELL (SHIELDED) ---
            if pos['amount'] > 0:
                min_target = pos['avg_price'] * (1 + PROFIT_MARGIN)
                max_reached = pos['max_price_reached']
                
                if max_reached >= min_target:
                    sell_trigger = max_reached * (1 - TRAILING_GAP)
                    
                    if price <= sell_trigger:
                        sell_amount = pos['amount']
                        order_success = True
                        
                        if not SIMULATION:
                            try:
                                base_coin = pair.split('/')[0]
                                real_balances = exchange.fetch_balance()
                                real_amount = real_balances.get(base_coin, {}).get('free', 0.0)
                                
                                sell_amount = min(sell_amount, real_amount)
                                sell_amount = float(exchange.amount_to_precision(pair, sell_amount))
                                
                                exchange.create_market_sell_order(pair, sell_amount)
                                extra_logs.append(f"[TRADE] ✅ ORDER EXECUTED: SELL {sell_amount} {pair}")
                                
                            except ccxt.ExchangeError as e:
                                err_str = str(e)
                                if '-2015' in err_str or 'Invalid API-key, IP, or permissions' in err_str:
                                    extra_logs.append(f"[ERROR] IP Rechazada por Binance (-2015) al vender {pair}.")
                                    send_telegram("🚨 **ERROR DE SEGURIDAD**: Binance ha rechazado la IP actual. Actualiza la IP.")
                                else:
                                    extra_logs.append(f"[ERROR] SELL API {pair}: {e}")
                                order_success = False
                            except ccxt.InsufficientFunds as e:
                                extra_logs.append(f"[ERROR] FUNDS: Tried to sell {pair} but no balance. {e}")
                                send_telegram(f"⚠️ **ALERTA BINANCE**\nFallo al vender {pair} (Fondos insuficientes o polvo).")
                                order_success = False
                            except Exception as e:
                                extra_logs.append(f"[ERROR] SELL API {pair}: {e}")
                                order_success = False

                        if order_success:
                            gross_total = sell_amount * price
                            net_total = gross_total * (1 - BINANCE_FEE) 
                            net_profit = net_total - pos['invested']
                            
                            telegram_msg = (
                                f"🟢 **VENTA TTP {pair}**\n"
                                f"Bruto: {gross_total:.2f}$\n"
                                f"Neto: {net_total:.2f}$\n"
                                f"Ganancia Limpia: {net_profit:.4f}$\n"
                                f"📈 Máx. Alcanzado: {max_reached:.2f}$\n"
                                f"🎯 Gatillo Ejecutado: {sell_trigger:.2f}$"
                            )
                            send_telegram(telegram_msg)
                            extra_logs.append(f"[TRADE] SELL completed for {pair} at {price}$.")
                            
                            state.data['realized_profit'] += net_profit
                            save_history("VENTA_TTP", pair, price, sell_amount, net_total, max_reached)
                            
                            pos['amount'] = 0.0
                            pos['invested'] = 0.0
                            pos['avg_price'] = 0.0
                            pos['max_price_reached'] = 0.0
                            state.save()
                            continue 
                    else:
                        extra_logs.append(f"[TRAILING] {pair} in profit zone. Max: {max_reached:.2f}$. Trigger: {sell_trigger:.2f}$")

            # --- BUY (SHIELDED + MACRO FILTER) ---
            if used_slots < MAX_SLOTS:
                rsi_entry_cond = rsi < MAX_RSI_ENTRY and is_uptrend
                dca_cond = False
                
                if pos['amount'] > 0:
                    dca_price = pos['avg_price'] * (1 - DCA_DROP_PERCENT)
                    if price < dca_price and is_uptrend:
                        dca_cond = True

                if (pos['amount'] == 0 and rsi_entry_cond) or dca_cond:
                    current_ema_200 = verify_macro_trend(pair)
                    
                    if price > current_ema_200:
                        is_buying = True
                        buy_type = "INICIAL" if pos['amount'] == 0 else "DCA"
                        
                        gross_amount = SLOT_SIZE / price
                        net_amount = gross_amount * (1 - BINANCE_FEE)
                        
                        try:
                            rounded_amount = float(exchange.amount_to_precision(pair, net_amount))
                        except:
                            rounded_amount = net_amount 
                        
                        order_success = True
                        
                        if not SIMULATION:
                            try:
                                real_balances = exchange.fetch_balance()
                                free_usdc = real_balances.get('USDC', {}).get('free', 0.0)
                                
                                if free_usdc < SLOT_SIZE:
                                    extra_logs.append(f"[ERROR] INSUFFICIENT USDC. Have {free_usdc}$, need {SLOT_SIZE}$.")
                                    send_telegram(f"🛑 **ALERTA LIQUIDEZ**\nIntento de compra de {pair} fallido.\nSaldo disponible: {free_usdc:.2f}$.")
                                    order_success = False
                                else:
                                    exchange.create_market_buy_order(pair, rounded_amount)
                                    extra_logs.append(f"[TRADE] ✅ ORDER EXECUTED: BUY {rounded_amount} {pair}")
                                    
                            except ccxt.ExchangeError as e:
                                err_str = str(e)
                                if '-2015' in err_str or 'Invalid API-key, IP, or permissions' in err_str:
                                    extra_logs.append(f"[ERROR] IP Rechazada por Binance (-2015) al comprar {pair}.")
                                    send_telegram("🚨 **ERROR DE SEGURIDAD**: Binance ha rechazado la IP actual. Actualiza la IP.")
                                else:
                                    extra_logs.append(f"[ERROR] BUY API {pair}: {e}")
                                order_success = False
                            except ccxt.InsufficientFunds as e:
                                extra_logs.append(f"[ERROR] FUNDS: Binance rejected buy for {pair}. {e}")
                                order_success = False
                            except Exception as e:
                                extra_logs.append(f"[ERROR] BUY API {pair}: {e}")
                                order_success = False

                        if order_success:
                            new_inv = pos['invested'] + SLOT_SIZE
                            new_amt = pos['amount'] + rounded_amount
                            new_avg = new_inv / new_amt
                            
                            send_telegram(f"🔵 **COMPRA {buy_type} {pair}**\nPrecio: {price}$\nRecibido Neto: {rounded_amount}\nRSI: {rsi:.1f} ↗️")
                            save_history("COMPRA", pair, price, rounded_amount, SLOT_SIZE, 0.0)
                            extra_logs.append(f"[TRADE] BUY completed for {pair} at {price}$.")
                            
                            pos['amount'] = new_amt
                            pos['invested'] = new_inv
                            pos['avg_price'] = new_avg
                            pos['max_price_reached'] = price
                            state.save()
                            used_slots += 1
                            
                    else:
                        extra_logs.append(f"[INFO] [{pair}] RSI/DCA signal detected, but blocked by Macro Downtrend (EMA 200 1H: {current_ema_200:.2f}$).")

        print(log_line)
        log_daily(log_line)
        for msg in extra_logs:
            print(msg)
            log_daily(msg)
            
        # ¡LO NUEVO! Antes de dormir, inyectamos los precios en el JSON para la Web
        state.save(live_prices=price_cache) 
            
        time.sleep(60)

    except Exception as e:
        err_msg = f"[ERROR] Main Loop: {e}"
        print(err_msg)
        log_daily(err_msg)
        time.sleep(10)
