"""SQLite: tablo şeması ve basit anahtar-değer deposu."""
import json
import os
import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS members (
    chat_id INTEGER PRIMARY KEY, name TEXT NOT NULL, added REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS calendars (
    chat_id INTEGER PRIMARY KEY, url TEXT NOT NULL, added REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    mac TEXT PRIMARY KEY, ip TEXT, host TEXT, name TEXT NOT NULL DEFAULT '',
    first_seen REAL NOT NULL, last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS speedtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
    down_mbps REAL, up_mbps REAL, ping_ms REAL, trigger TEXT NOT NULL DEFAULT 'gece'
);
CREATE TABLE IF NOT EXISTS prefs (
    chat_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (chat_id, key)
);
CREATE TABLE IF NOT EXISTS outages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
    started REAL NOT NULL, ended REAL NOT NULL
);
"""


def db() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    with db() as con:
        con.executescript(SCHEMA)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(devices)")}
        if "net" not in cols:      # eski sürümden geçiş: cihazlar hangi Wi-Fi ağında görüldü
            con.execute("ALTER TABLE devices ADD COLUMN net TEXT NOT NULL DEFAULT ''")
        if "note" not in {r["name"] for r in con.execute("PRAGMA table_info(outages)")}:
            con.execute("ALTER TABLE outages ADD COLUMN note TEXT NOT NULL DEFAULT ''")   # örn. reset sebebi


def kv_get(k: str):
    with db() as con:
        row = con.execute("SELECT v FROM kv WHERE k = ?", (k,)).fetchone()
    return row["v"] if row else None


def kv_set(k: str, v) -> None:
    with db() as con:
        con.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (k, str(v)))


def kv_json(k: str, default):
    try:
        return json.loads(kv_get(k) or "null") or default
    except json.JSONDecodeError:
        return default


def pref_get(chat_id: int, key: str, default: str = "") -> str:
    with db() as con:
        row = con.execute("SELECT value FROM prefs WHERE chat_id = ? AND key = ?", (chat_id, key)).fetchone()
    return row["value"] if row else default


def pref_set(chat_id: int, key: str, value: str) -> None:
    with db() as con:
        con.execute("INSERT OR REPLACE INTO prefs (chat_id, key, value) VALUES (?, ?, ?)", (chat_id, key, value))
