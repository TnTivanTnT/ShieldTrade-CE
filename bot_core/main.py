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

# --- CONFIGURACIÓN DE USUARIO ---
SIMULACION = True
PARES = ['SOL/EUR', 'ETH/EUR']
PRESUPUESTO_TOTAL = 50.0
TAMANO_SLOT = 10.5
MAX_SLOTS = int(PRESUPUESTO_TOTAL // TAMANO_SLOT)

# Estrategia DCA + FILTRO RSI + TRAILING
RSI_MAX_ENTRADA = 45
CAIDA_PARA_RECOMPRA = 0.03
MARGEN_GANANCIA = 0.015
TRAILING_GAP = 0.003
COMISION_BINANCE = 0.001
DIAS_RETENCION_LOGS = 15

# V4.1: Filtro Macro
EMA_MACRO_PERIOD = 200
EMA_MACRO_TIMEFRAME = '1h'

# --- CREDENCIALES ---
TELEGRAM_TOKEN = ""
TELEGRAM_CHAT_ID = ""
API_KEY = ''               
SECRET_KEY = ''  

# --- RUTAS ---
ARCHIVO_CSV = "/app/historial_trading.csv"
ARCHIVO_ESTADO = "/app/estado_bot.json"
CARPETA_LOGS = "/app/logs"

# --- INICIALIZACIÓN ---
exchange = ccxt.binance({'enableRateLimit': True})
if not SIMULACION:
    exchange.apiKey = API_KEY
    exchange.secret = SECRET_KEY

def log_diario(mensaje):
    try:
        os.makedirs(CARPETA_LOGS, exist_ok=True)
        nombre_archivo = f"{CARPETA_LOGS}/log_{datetime.now().strftime('%Y-%m-%d')}.txt"
        with open(nombre_archivo, "a", encoding="utf-8") as f:
            f.write(f"{mensaje}\n")
            
        limite_fecha = datetime.now() - timedelta(days=DIAS_RETENCION_LOGS)
        archivos_log = glob.glob(f"{CARPETA_LOGS}/log_*.txt")
        for archivo in archivos_log:
            fecha_str = archivo.split('_')[-1].replace('.txt', '')
            try:
                fecha_archivo = datetime.strptime(fecha_str, '%Y-%m-%d')
                if fecha_archivo < limite_fecha:
                    os.remove(archivo)
            except: pass
    except Exception as e: 
        print(f"[ERROR] Sistema de logs falló: {e}")

try:
    exchange.load_markets()
except Exception as e:
    msg = f"[ERROR] Aviso: No se pudieron cargar los mercados inicialmente: {e}"
    print(msg)
    log_diario(msg)

precios_cache = {par: 0.0 for par in PARES}

# --- GESTOR DE MEMORIA ---
class GestorEstado:
    def __init__(self):
        self.datos = {
            "balance_inicial": PRESUPUESTO_TOTAL,
            "balance_ayer": PRESUPUESTO_TOTAL,
            "fecha_ultimo_reporte": "",
            "beneficio_realizado_acumulado": 0.0,
            "cartera": {par: {"cantidad": 0.0, "precio_medio": 0.0, "invertido": 0.0, "precio_maximo_alcanzado": 0.0} for par in PARES}
        }
        self.cargar()

    def cargar(self):
        if os.path.exists(ARCHIVO_ESTADO):
            try:
                with open(ARCHIVO_ESTADO, 'r') as f:
                    data_cargada = json.load(f)
                    for k, v in data_cargada.items():
                        if k == "cartera":
                            for par, info in v.items():
                                if "precio_maximo_alcanzado" not in info:
                                    info["precio_maximo_alcanzado"] = 0.0
                        self.datos[k] = v
                    if "beneficio_realizado_acumulado" not in self.datos:
                        self.datos["beneficio_realizado_acumulado"] = 0.0
            except:
                msg = "[ERROR] Error JSON, iniciando de cero."
                print(msg)
                log_diario(msg)

    def guardar(self):
        try:
            with open(ARCHIVO_ESTADO, 'w') as f:
                json.dump(self.datos, f, indent=4)
        except Exception as e:
            msg = f"[ERROR] ERROR JSON al guardar: {e}"
            print(msg)
            log_diario(msg)

    def get_slots_usados(self):
        total_invertido = sum(d['invertido'] for d in self.datos['cartera'].values())
        return int(total_invertido // TAMANO_SLOT)

estado = GestorEstado()

# --- FUNCIONES DE MERCADO ---
def enviar_telegram(mensaje):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "Markdown"})
    except: pass

def calcular_patrimonio_total():
    dinero_libre = (PRESUPUESTO_TOTAL + estado.datos['beneficio_realizado_acumulado']) - sum(d['invertido'] for d in estado.datos['cartera'].values())
    valor_monedas = sum(info['cantidad'] * precios_cache.get(par, 0.0) for par, info in estado.datos['cartera'].items() if info['cantidad'] > 0)
    return dinero_libre + valor_monedas

def guardar_historial(accion, par, precio, cantidad, total, precio_max_trade=0.0):
    try:
        existe = os.path.exists(ARCHIVO_CSV)
        with open(ARCHIVO_CSV, 'a', newline='') as f:
            writer = csv.writer(f)
            if not existe:
                writer.writerow(["Fecha", "Par", "Accion", "Precio", "Cantidad", "Total_EUR", "Balance_Realizado", "Precio_Maximo_Trade"])
            
            balance_realizado_actual = PRESUPUESTO_TOTAL + estado.datos['beneficio_realizado_acumulado']
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                par, accion, precio, cantidad, total, balance_realizado_actual, precio_max_trade
            ])
    except Exception as e:
        log_diario(f"[ERROR] Error CSV: {e}")

def obtener_datos_mercado(par):
    try:
        ohlcv = exchange.fetch_ohlcv(par, timeframe='5m', limit=100)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        rsi = df.ta.rsi(length=14).iloc[-1]
        precio_actual = df['close'].iloc[-1]
        precio_anterior = df['close'].iloc[-2]
        
        precios_cache[par] = precio_actual
        return precio_actual, rsi, precio_anterior
        
    except ccxt.NetworkError as e:
        log_diario(f"[ERROR] Error de red Binance ({par}): Ignorando ciclo...")
        return 0, 100, 999999
    except ccxt.ExchangeError as e:
        log_diario(f"[ERROR] Error interno Binance ({par}): Ignorando ciclo...")
        return 0, 100, 999999
    except Exception as e:
        log_diario(f"[ERROR] Error crítico obteniendo datos ({par}): {e}")
        return 0, 100, 999999

def verificar_tendencia_macro(par):
    """ V4.1: Calcula la EMA 200 en 1H para confirmar tendencia alcista global """
    try:
        # Límite a 250 para asegurar que la EMA 200 tiene suficientes datos previos
        ohlcv = exchange.fetch_ohlcv(par, timeframe=EMA_MACRO_TIMEFRAME, limit=250)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        ema_200 = df.ta.ema(length=EMA_MACRO_PERIOD).iloc[-1]
        
        # Limpieza agresiva de memoria para el i7 1ª Gen
        del df
        del ohlcv
        gc.collect()
        
        return ema_200
    except Exception as e:
        log_diario(f"[ERROR] Fallo al calcular EMA 200 Macro para {par}: {e}")
        # En caso de error de red, devolvemos un número altísimo para bloquear compras por seguridad
        return float('inf')

# --- BUCLE PRINCIPAL ---
msg_inicio = "[INFO] 🤖 BOT V4.1: Protector (TTP + EMA200 1H)"
print(msg_inicio)
log_diario(msg_inicio)
enviar_telegram("🚀 **Bot V4.1 Iniciado**\nModo: Preparado para Producción 🛡️\nMotor: Trailing Take Profit\nFiltro: Tendencia Macro (EMA200)")

while True:
    try:
        ahora = datetime.now()
        slots_ocupados = estado.get_slots_usados()
        
        if ahora.hour == 10 and ahora.minute == 0 and estado.datos['fecha_ultimo_reporte'] != ahora.strftime("%Y-%m-%d"):
            for par in PARES:
                obtener_datos_mercado(par)
            
            patrimonio = calcular_patrimonio_total()
            balance_ini = estado.datos['balance_inicial']
            balance_ayer = estado.datos['balance_ayer']
            
            b_diario = patrimonio - balance_ayer
            b_historico = patrimonio - balance_ini
            
            msg_resumen = (
                f"📅 **Resumen Diario**\n"
                f"Dinero inicial: {balance_ini:.2f}€\n"
                f"Actual (Flotante): {patrimonio:.2f}€\n"
                f"Beneficio Realizado: {'+' if estado.datos['beneficio_realizado_acumulado']>0 else ''}{estado.datos['beneficio_realizado_acumulado']:.2f}€\n"
                f"Balance diario: {'+' if b_diario>0 else ''}{b_diario:.2f}€\n"
                f"Balance histórico: {'+' if b_historico>0 else ''}{b_historico:.2f}€"
            )
            enviar_telegram(msg_resumen)
            
            estado.datos['fecha_ultimo_reporte'] = ahora.strftime("%Y-%m-%d")
            estado.datos['balance_ayer'] = patrimonio
            estado.guardar()

        log_linea = f"[INFO] [{ahora.strftime('%H:%M')}] "
        logs_adicionales = []

        for par in PARES:
            precio, rsi, precio_ant = obtener_datos_mercado(par)
            if precio == 0: continue
            
            posicion = estado.datos['cartera'][par]
            tendencia_alcista = precio > precio_ant
            icono_tendencia = "↗️" if tendencia_alcista else "↘️"
            
            log_linea += f"{par.split('/')[0]}:{precio:.1f}€({rsi:.0f}){icono_tendencia} | "

            if posicion['cantidad'] > 0:
                if precio > posicion['precio_maximo_alcanzado']:
                    posicion['precio_maximo_alcanzado'] = precio
                    estado.guardar()

            # --- VENTA TTP (SHIELDED - Sin Filtro Macro) ---
            if posicion['cantidad'] > 0:
                objetivo_minimo = posicion['precio_medio'] * (1 + MARGEN_GANANCIA)
                maximo_logrado = posicion['precio_maximo_alcanzado']
                
                if maximo_logrado >= objetivo_minimo:
                    gatillo_venta = maximo_logrado * (1 - TRAILING_GAP)
                    
                    if precio <= gatillo_venta:
                        cantidad_a_vender = posicion['cantidad']
                        orden_completada = True
                        
                        if not SIMULACION:
                            try:
                                moneda_base = par.split('/')[0]
                                balance_real = exchange.fetch_balance()
                                cantidad_real = balance_real[moneda_base]['free']
                                
                                cantidad_a_vender = min(cantidad_a_vender, cantidad_real)
                                cantidad_a_vender = float(exchange.amount_to_precision(par, cantidad_a_vender))
                                
                                exchange.create_market_sell_order(par, cantidad_a_vender)
                                logs_adicionales.append(f"[TRADE] ✅ ORDEN EJECUTADA: VENTA {cantidad_a_vender} {par}")
                                
                            except ccxt.InsufficientFunds as e:
                                logs_adicionales.append(f"[ERROR] FONDOS: Intentado vender {par} pero no hay saldo. {e}")
                                enviar_telegram(f"⚠️ **ALERTA BINANCE**\nFallo al vender {par} (Fondos insuficientes o polvo).")
                                orden_completada = False
                            except Exception as e:
                                logs_adicionales.append(f"[ERROR] API VENTA {par}: {e}")
                                orden_completada = False

                        if orden_completada:
                            total_bruto = cantidad_a_vender * precio
                            total_neto = total_bruto * (1 - COMISION_BINANCE) 
                            beneficio_neto = total_neto - posicion['invertido']
                            
                            telegram_msg = (
                                f"🟢 **VENTA TTP {par}**\n"
                                f"Bruto: {total_bruto:.2f}€\n"
                                f"Neto: {total_neto:.2f}€\n"
                                f"Ganancia Limpia: {beneficio_neto:.4f}€\n"
                                f"📈 Máx. Alcanzado: {maximo_logrado:.2f}€\n"
                                f"🎯 Gatillo Ejecutado: {gatillo_venta:.2f}€"
                            )
                            enviar_telegram(telegram_msg)
                            logs_adicionales.append(f"[TRADE] VENTA completada en {par} a {precio}€.")
                            
                            estado.datos['beneficio_realizado_acumulado'] += beneficio_neto
                            guardar_historial("VENTA_TTP", par, precio, cantidad_a_vender, total_neto, maximo_logrado)
                            
                            posicion['cantidad'] = 0.0
                            posicion['invertido'] = 0.0
                            posicion['precio_medio'] = 0.0
                            posicion['precio_maximo_alcanzado'] = 0.0
                            estado.guardar()
                            continue 
                    else:
                        logs_adicionales.append(f"[TRAILING] {par} en zona de profit. Máximo: {maximo_logrado:.2f}€. Gatillo en: {gatillo_venta:.2f}€")

            # --- COMPRA (SHIELDED - CON FILTRO MACRO) ---
            if slots_ocupados < MAX_SLOTS:
                condicion_entrada_rsi = rsi < RSI_MAX_ENTRADA and tendencia_alcista
                condicion_dca = False
                
                if posicion['cantidad'] > 0:
                    precio_dca = posicion['precio_medio'] * (1 - CAIDA_PARA_RECOMPRA)
                    if precio < precio_dca and tendencia_alcista:
                        condicion_dca = True

                # Si el RSI o el DCA piden entrar, verificamos al "Jefe Final" (EMA 200)
                if (posicion['cantidad'] == 0 and condicion_entrada_rsi) or condicion_dca:
                    ema_200_actual = verificar_tendencia_macro(par)
                    
                    if precio > ema_200_actual:
                        # Vía libre para comprar
                        comprar = True
                        tipo = "INICIAL" if posicion['cantidad'] == 0 else "DCA"
                        
                        cantidad_bruta = TAMANO_SLOT / precio
                        cantidad_neta = cantidad_bruta * (1 - COMISION_BINANCE)
                        
                        try:
                            cantidad_redondeada = float(exchange.amount_to_precision(par, cantidad_neta))
                        except:
                            cantidad_redondeada = cantidad_neta 
                        
                        orden_completada = True
                        
                        if not SIMULACION:
                            try:
                                balance_real = exchange.fetch_balance()
                                euros_libres = balance_real['EUR']['free']
                                
                                if euros_libres < TAMANO_SLOT:
                                    logs_adicionales.append(f"[ERROR] SALDO EUR INSUFICIENTE. Tienes {euros_libres}€, necesitas {TAMANO_SLOT}€.")
                                    enviar_telegram(f"🛑 **ALERTA LIQUIDEZ**\nIntento de compra de {par} fallido.\nSaldo disponible: {euros_libres:.2f}€.")
                                    orden_completada = False
                                else:
                                    exchange.create_market_buy_order(par, cantidad_redondeada)
                                    logs_adicionales.append(f"[TRADE] ✅ ORDEN EJECUTADA: COMPRA {cantidad_redondeada} {par}")
                                    
                            except ccxt.InsufficientFunds as e:
                                logs_adicionales.append(f"[ERROR] FONDOS: API Binance rechazó la compra de {par}. {e}")
                                orden_completada = False
                            except Exception as e:
                                logs_adicionales.append(f"[ERROR] API COMPRA {par}: {e}")
                                orden_completada = False

                        if orden_completada:
                            nuevo_inv = posicion['invertido'] + TAMANO_SLOT
                            nuevo_cant = posicion['cantidad'] + cantidad_redondeada
                            nuevo_pm = nuevo_inv / nuevo_cant
                            
                            enviar_telegram(f"🔵 **COMPRA {tipo} {par}**\nPrecio: {precio}€\nRecibido Neto: {cantidad_redondeada}\nRSI: {rsi:.1f} ↗️")
                            guardar_historial("COMPRA", par, precio, cantidad_redondeada, TAMANO_SLOT, 0.0)
                            logs_adicionales.append(f"[TRADE] COMPRA completada en {par} a {precio}€.")
                            
                            posicion['cantidad'] = nuevo_cant
                            posicion['invertido'] = nuevo_inv
                            posicion['precio_medio'] = nuevo_pm
                            posicion['precio_maximo_alcanzado'] = precio
                            estado.guardar()
                            slots_ocupados += 1
                            
                    else:
                        # Bloqueo de la EMA 200
                        logs_adicionales.append(f"[INFO] [{par}] Señal RSI/DCA detectada, pero bloqueada por Tendencia Macro Bajista (EMA 200 1H: {ema_200_actual:.2f}€).")

        print(log_linea)
        log_diario(log_linea)
        for msg in logs_adicionales:
            print(msg)
            log_diario(msg)
            
        time.sleep(60)

    except Exception as e:
        msg_err = f"[ERROR] Bucle General: {e}"
        print(msg_err)
        log_diario(msg_err)
        time.sleep(10)

