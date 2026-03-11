from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import json
import os
import sqlite3
import time

app = FastAPI(title="ShieldTrade API")
STATE_FILE = "/app/bot_state.json"
DB_PATH = "/app/shieldtrade.db"

# 1. Ruta principal: Carga la Interfaz Visual (HTML)
@app.get("/")
def serve_app():
    return FileResponse("/app/static/index.html")

# 2. Ruta de estado: El motor interno para los números actuales
@app.get("/status")
def get_status():
    try:
        if not os.path.exists(STATE_FILE):
            return {"error": "Estado no disponible todavía"}
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 3. Ruta de historial: Extrae datos de SQLite para la futura gráfica
@app.get("/history")
def get_history():
    try:
        if not os.path.exists(DB_PATH):
            return []
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM equity_history ORDER BY timestamp DESC LIMIT 168") # Última semana (168 horas)
        rows = c.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 4. Ruta de salud: Monitorización del bot
@app.get("/health")
def health_check():
    if not os.path.exists(STATE_FILE):
        return {"status": "error", "message": "Archivo de estado no encontrado"}
    
    last_modified = os.path.getmtime(STATE_FILE)
    age_seconds = time.time() - last_modified
    
    # Si hace más de 5 minutos que el bot no actualiza el JSON
    if age_seconds > 300: 
        return {"status": "warning", "message": f"Última escritura hace {int(age_seconds)}s"}
    return {"status": "healthy", "message": "Bot escribiendo con normalidad"}