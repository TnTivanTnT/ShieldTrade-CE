from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import json
import os
import sqlite3
import time

app = FastAPI(title="ShieldTrade API")
STATE_FILE = "/app/bot_state.json"
DB_PATH = "/app/shieldtrade.db"

@app.get("/")
def serve_app():
    return FileResponse("/app/static/index.html")

@app.get("/status")
def get_status():
    try:
        if not os.path.exists(STATE_FILE): return {"error": "Esperando datos..."}
        with open(STATE_FILE, "r") as f: return json.load(f)
    except: raise HTTPException(status_code=500, detail="Error de lectura")

@app.get("/history")
def get_history(range: str = "1d"):
    try:
        hours_map = {"1d": 24, "1w": 168, "1m": 720, "all": 9999}
        hours = hours_map.get(range, 24)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM equity_history WHERE timestamp >= datetime('now', ?) ORDER BY timestamp ASC", (f'-{hours} hours',))
        rows = c.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except: return []

@app.get("/health")
def health_check():
    try:
        last_mod = os.path.getmtime(STATE_FILE)
        age = time.time() - last_mod
        return {"status": "healthy" if age < 300 else "warning", "age": int(age)}
    except: return {"status": "error"}
