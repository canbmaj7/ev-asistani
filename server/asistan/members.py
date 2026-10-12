"""Bildirim alanlar: sahip + davetle katılan üyeler."""
import time

from . import config
from .db import db


def recipients() -> list[int]:
    with db() as con:
        rows = con.execute("SELECT chat_id FROM members ORDER BY added").fetchall()
    return [config.OWNER_ID] + [r["chat_id"] for r in rows if r["chat_id"] != config.OWNER_ID]


def is_member(chat_id: int) -> bool:
    return chat_id in recipients()


def is_owner(chat_id: int) -> bool:
    return chat_id == config.OWNER_ID


def add(chat_id: int, name: str) -> None:
    with db() as con:
        con.execute("INSERT OR REPLACE INTO members (chat_id, name, added) VALUES (?, ?, ?)",
                    (chat_id, name, time.time()))


def remove(chat_id: int) -> bool:
    with db() as con:
        return con.execute("DELETE FROM members WHERE chat_id = ?", (chat_id,)).rowcount > 0


def listing() -> list:
    with db() as con:
        return con.execute("SELECT chat_id, name FROM members ORDER BY added").fetchall()


def count() -> int:
    with db() as con:
        return con.execute("SELECT COUNT(*) FROM members").fetchone()[0]
