import sqlite3
import os

DB_PATH = "/app/shieldtrade.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS equity_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  total_usdc REAL,
                  sol_price REAL,
                  eth_price REAL)''')
    conn.commit()
    conn.close()

def save_snapshot(total_usdc, sol_price, eth_price):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO equity_history (total_usdc, sol_price, eth_price) VALUES (?, ?, ?)",
                  (total_usdc, sol_price, eth_price))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ERROR] No se pudo guardar en SQLite: {e}")
