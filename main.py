import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import time
import json
import os
import csv
import numpy as np
from datetime import datetime
from dotenv import load_dotenv
import database
from risk_manager import DrawdownGuard, compute_atr, compute_atr_trailing_stop, check_emergency_stop, volume_confirmation
from signals import VolatilityRegime, bollinger_squeeze, stoch_rsi, macd_divergence, btc_shield, compute_entry_score
from performance import PerformanceTracker

if os.path.exists("/app/.env"):
    load_dotenv("/app/.env")
else:
    load_dotenv() # Probar carga local o desde variables de entorno de Docker


# --- CREDENCIALES ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

# --- CONFIGURACIÓN MAESTRA V5.2.1 ---
SIMULATION = True
TOTAL_BUDGET = 60.72      # Tu capital real tras la venta
BASE_SLOT_USDC = 14.40     
MAX_SLOTS_LIMIT = 6        

PAIRS = ['SOL/USDC', 'ETH/USDC']

# --- [V5.2.1] Parámetros dinámicos (defaults — se adaptan por VolatilityRegime) ---
Z_SCORE_ENTRY = -2.0     
DCA_DROP_PERCENT = 0.03
PROFIT_MARGIN = 0.015
ATR_TRAILING_MULT = 2.0     # Reemplaza TRAILING_GAP fijo
EMERGENCY_STOP_MULT = 3.0   # Stop-loss de emergencia: 3×ATR

EMA_MACRO_PERIOD = 200
EMA_MACRO_TIMEFRAME = '1h'

# --- [V5.2.1] Seguridad ---
MAX_DRAWDOWN_PCT = 0.15     # Pausa el bot si drawdown > 15%
VOLUME_THRESHOLD = 1.5       # Volumen necesario: 1.5× media
ENTRY_SCORE_THRESHOLD = 3    # Mínimo 3/6 señales para entrar

STATE_FILE = "/app/bot_state.json"
CSV_FILE = "/app/trading_history_usdc.csv"
LOGS_FOLDER = "/app/logs"
PERF_FILE = "/app/performance.json"

database.init_db()
exchange = ccxt.binance({
    'enableRateLimit': True,
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'options': {'defaultType': 'spot'}
})

# --- [V5.2.1] Inicializar módulos ---
drawdown_guard = DrawdownGuard(max_drawdown_pct=MAX_DRAWDOWN_PCT)
volatility_regime = VolatilityRegime()
perf_tracker = PerformanceTracker(save_path=PERF_FILE)

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
        self.data = {
            "initial_balance": TOTAL_BUDGET,
            "realized_profit": 0.0,
            "portfolio": {pair: {"amount": 0.0, "invested": 0.0, "avg_price": 0.0, "max_price_reached": 0.0} for pair in PAIRS},
            "yesterday_balance": TOTAL_BUDGET
        }
        self.load()
        if self.data.get('initial_balance') != TOTAL_BUDGET:
            self.data['initial_balance'] = TOTAL_BUDGET
            self.save()

    def load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f: 
                    loaded_data = json.load(f)
                    if loaded_data:
                        self.data.update(loaded_data)
            except Exception as e:
                print(f"[ERROR] Error cargando estado: {e}")

    def save(self, live_prices=None):
        if live_prices: self.data["current_market_prices"] = live_prices
        with open(STATE_FILE + ".tmp", 'w') as f: json.dump(self.data, f, indent=4)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)

state = StateManager()

# --- [V5.2.1] Restaurar peak del DrawdownGuard desde estado guardado ---
if 'drawdown_peak' in state.data:
    drawdown_guard.set_peak(state.data['drawdown_peak'])


def get_market_data(pair):
    """Fetch market data with extended history for ATR/Bollinger calculations."""
    try:
        ticker = exchange.fetch_ticker(pair)
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='5m', limit=200)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # --- CÁLCULO Z-SCORE ---
        df['sma'] = df['close'].rolling(window=20).mean()
        df['std'] = df['close'].rolling(window=20).std()
        current_close = df['close'].iloc[-1]
        last_sma = df['sma'].iloc[-1]
        last_std = df['std'].iloc[-1]
        
        z_score = (current_close - last_sma) / last_std if last_std > 0 else 0
        return ticker['last'], round(z_score, 2), df['close'].iloc[-2], df
    except:
        return 0, 0, 0, None

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
send_telegram(
    f"🛡️ **ShieldTrade V5.2.1 — Simons Edition + Safety**\n"
    f"Z-Score Activo: {Z_SCORE_ENTRY} σ\n"
    f"🆕 ATR Trailing Stop: {ATR_TRAILING_MULT}× | Emergency Stop: {EMERGENCY_STOP_MULT}×\n"
    f"🆕 Drawdown Guard: {MAX_DRAWDOWN_PCT*100:.0f}% | Multi-Signal Consensus"
)

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
            price, z_score, prev_price, df = get_market_data(pair)
            if price == 0 or df is None: continue
            price_cache[pair] = {"price": price, "z_score": z_score}
            pos = state.data['portfolio'][pair]

            # --- [V5.2.1] Detectar régimen de volatilidad ---
            regime_name, regime_params = volatility_regime.detect(df)
            active_z_entry = regime_params['z_entry']
            active_profit_margin = regime_params['profit_margin']
            active_atr_mult = regime_params['atr_mult']
            active_dca_drop = regime_params['dca_drop']

            log_line += f"{pair.split('/')[0]}:{z_score}σ({regime_name[0].upper()}) | "

            # --- [V5.2.1] Calcular ATR ---
            trailing_stop_price, current_atr = compute_atr_trailing_stop(df, period=14, multiplier=active_atr_mult)

            # ============================================
            # VENTA — ATR Trailing Take Profit + Emergency Stop
            # ============================================
            if pos['amount'] > 0:
                if price > pos['max_price_reached']: pos['max_price_reached'] = price
                target = pos['avg_price'] * (1 + active_profit_margin)

                # --- [V5.2.1] Emergency Stop-Loss (3×ATR) ---
                emergency_triggered, emergency_price = check_emergency_stop(
                    pos['avg_price'], price, current_atr, EMERGENCY_STOP_MULT
                )

                should_sell = False
                sell_reason = ""

                if emergency_triggered:
                    # 🔴 CORTAR PÉRDIDAS — prioridad máxima
                    should_sell = True
                    sell_reason = f"🔴 EMERGENCY STOP | Price={price} < Stop={emergency_price:.2f}"
                elif price >= target and price <= trailing_stop_price:
                    # 🟢 Trailing Take Profit con ATR dinámico
                    should_sell = True
                    sell_reason = f"🟢 TTP ATR | Price={price} <= Trail={trailing_stop_price:.2f}"

                if should_sell:
                    bal = exchange.fetch_balance()
                    sell_amt = float(exchange.amount_to_precision(pair, bal[pair.split('/')[0]]['free']))
                    order = exchange.create_market_sell_order(pair, sell_amt)
                    profit = float(order['cost']) - pos['invested']
                    state.data['realized_profit'] += profit
                    with open(CSV_FILE, 'a', newline='') as f:
                        csv.writer(f).writerow([now.strftime('%Y-%m-%d %H:%M:%S'), pair, 'SELL', price, sell_amt, profit])
                    
                    # [V5.2.1] Track performance
                    perf_tracker.add_trade(pair, 'SELL', price, sell_amt, float(order['cost']), profit=profit)
                    
                    send_telegram(f"{sell_reason}\n{pair} | P&L: {profit:+.2f}$")
                    log_daily(f"SELL {pair} {profit:+.2f}$ ({sell_reason})")
                    pos.update({"amount": 0.0, "invested": 0.0, "avg_price": 0.0, "max_price_reached": 0.0})
                    state.save()

            # ============================================
            # COMPRA / DCA — Multi-Signal Consensus
            # ============================================
            invested_total = sum(d['invested'] for d in state.data['portfolio'].values())
            used_slots = int(round(invested_total / current_slot_size)) if current_slot_size > 0 else 0
            
            if used_slots < current_max_slots:
                is_dca = pos['amount'] > 0 and price < (pos['avg_price'] * (1 - active_dca_drop))
                is_new = pos['amount'] == 0 and z_score < active_z_entry and price > prev_price
                
                if is_new or is_dca:
                    # --- [V5.2.1] Multi-Signal Consensus ---
                    vol_ok, vol_ratio = volume_confirmation(df, threshold=VOLUME_THRESHOLD)
                    is_squeeze, pct_b, bbw = bollinger_squeeze(df)
                    is_stoch, k_val, d_val = stoch_rsi(df)
                    is_macd, hist_val = macd_divergence(df)

                    should_enter, score, score_details = compute_entry_score(
                        df, price, z_score, vol_ok, is_squeeze, is_stoch, is_macd
                    )

                    # DCA bypasses consensus (averaging down is always valid if drop is confirmed)
                    # But new entries require consensus + macro filter
                    proceed = False
                    if is_dca:
                        proceed = True  # DCA siempre procede
                        log_daily(f"DCA {pair} | {score_details}")
                    elif is_new and should_enter and price > verify_macro_trend(pair):
                        proceed = True
                        log_daily(f"BUY {pair} | {score_details}")

                    # --- [V5.2.1] Drawdown check antes de comprar ---
                    if proceed and drawdown_guard.is_paused:
                        log_daily(f"⚠️ BLOCKED by DrawdownGuard: {pair}")
                        send_telegram(f"⚠️ Entrada bloqueada por Drawdown Guard\n{pair} | {score_details}")
                        proceed = False

                    if proceed:
                        buy_qty = float(exchange.amount_to_precision(pair, current_slot_size/price))
                        order = exchange.create_market_buy_order(pair, buy_qty)
                        pos['invested'] += float(order['cost'])
                        pos['amount'] += float(order['filled'])
                        pos['avg_price'] = pos['invested'] / pos['amount']
                        pos['max_price_reached'] = price
                        with open(CSV_FILE, 'a', newline='') as f:
                            csv.writer(f).writerow([now.strftime('%Y-%m-%d %H:%M:%S'), pair, 'BUY', price, order['filled'], order['cost']])
                        
                        # [V5.2.1] Track performance
                        perf_tracker.add_trade(pair, 'BUY', price, float(order['filled']), float(order['cost']))

                        emoji = '🔵' if not is_dca else '🟡'
                        label = 'DCA' if is_dca else 'BUY'
                        send_telegram(
                            f"{emoji} {label} {pair} | Z:{z_score}σ | Régimen:{regime_name}\n"
                            f"📊 {score_details}"
                        )
                        state.save()

        # --- [V5.2.1] Equity & Drawdown Monitoring ---
        equity = sync_balance(price_cache)
        should_pause, current_dd = drawdown_guard.update(equity)
        state.data['drawdown_peak'] = drawdown_guard.peak_equity  # Persist peak

        # [V5.2.1] Equity snapshot for Sharpe/Sortino
        if now.minute % 15 == 0 and state.data.get('last_perf_snapshot_min') != now.minute:
            perf_tracker.add_equity_snapshot(equity)
            state.data['last_perf_snapshot_min'] = now.minute

        if should_pause and not state.data.get('drawdown_alerted'):
            dd_status = drawdown_guard.get_status(equity)
            send_telegram(
                f"🛑 **DRAWDOWN GUARD ACTIVATED**\n"
                f"Peak: {dd_status['peak']:.2f}$ → Current: {dd_status['current']:.2f}$\n"
                f"Drawdown: {dd_status['drawdown_pct']:.1f}% (Límite: {MAX_DRAWDOWN_PCT*100:.0f}%)\n"
                f"⚠️ Todas las entradas están BLOQUEADAS"
            )
            state.data['drawdown_alerted'] = True
            state.save()
        elif not should_pause and state.data.get('drawdown_alerted'):
            send_telegram(f"✅ Drawdown Guard desactivado. Trading resumido.")
            state.data['drawdown_alerted'] = False
            state.save()

        # Reporte Diario (mejorado con métricas)
        if now.hour >= 10 and state.data.get('last_report_date') != now.strftime("%Y-%m-%d"):
            yesterday = state.data.get('yesterday_balance', equity)
            dd_status = drawdown_guard.get_status(equity)
            perf_report = perf_tracker.format_report()
            send_telegram(
                f"📅 **Daily Report V5.2.1**\n"
                f"Equity: {equity:.2f}$ | Profit: {equity-yesterday:+.2f}$\n"
                f"Drawdown: {dd_status['drawdown_pct']:.1f}% (max {MAX_DRAWDOWN_PCT*100:.0f}%)\n"
                f"---\n{perf_report}"
            )
            state.data['last_report_date'] = now.strftime("%Y-%m-%d")
            state.data['yesterday_balance'] = equity; state.save()

        if now.minute == 0 and state.data.get('last_db_snapshot_hour') != now.hour:
            database.save_snapshot(equity, price_cache.get('SOL/USDC', {}).get('price', 0), price_cache.get('ETH/USDC', {}).get('price', 0))
            state.data['last_db_snapshot_hour'] = now.hour; state.save()

        if now.minute != last_log_minute:
            dd_str = f"DD:{current_dd*100:.1f}%"
            regime_str = "Regime:" + regime_name if 'regime_name' in dir() else ""
            full_log = f"{log_line}{dd_str} | {regime_str}"
            print(full_log); log_daily(full_log); last_log_minute = now.minute
        
        state.save(live_prices=price_cache)
        time.sleep(10)
    except Exception as e: print(f"Error: {e}"); time.sleep(30)
