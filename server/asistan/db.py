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
