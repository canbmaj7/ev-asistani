"""Kişisel takvim: herkes /takvim ile kendi .ics linkini bağlar; yalnızca kendi özetinde görünür."""
import asyncio
import time
from datetime import datetime

from . import config
from . import telegram as tg
from .db import db
from .sources import calendar as cal_src
from .sources.common import client

WAIT_SEC = 600
awaiting: dict[int, float] = {}    # /takvim yazıp link bekleyenler → son geçerlilik

HOWTO = (
    "📅 Takvimini bağlarsan sabah özetinde o günün etkinliklerini görürsün.\n\n"
    "Google Takvim (bilgisayardan):\n"
    "1) calendar.google.com → sol menüde takviminin yanındaki ⋮ → Ayarlar ve paylaşım\n"
    "2) En altta \"iCal biçiminde gizli adres\" satırındaki linki kopyala\n"
    "3) Linki bu sohbete gönder (10 dakika içinde)\n\n"
    "iPhone/Outlook takvimlerinin .ics linki de olur. Link, takvimini parolasız okutur: "
    "kimseyle paylaşma; ben kaydettikten sonra mesajını sohbetten silerim.\n"
    "Kaldırmak için: /takvim sil"
)


def get(chat_id: int) -> str:
    with db() as con:
        row = con.execute("SELECT url FROM calendars WHERE chat_id = ?", (chat_id,)).fetchone()
    return row["url"] if row else ""


def expects_link(chat_id: int, text: str) -> bool:
    """/takvim sonrası komutsuz gönderilen link mi?"""
    return awaiting.get(chat_id, 0) > time.time() and text.lower().startswith(("https://", "webcal://"))


async def save(chat_id: int, url: str, message_id: int | None) -> None:
    awaiting.pop(chat_id, None)
    deleted = await tg.delete_message(chat_id, message_id)      # gizli link sohbette kalmasın
    url = url.strip().replace("webcal://", "https://", 1)
    if not await asyncio.to_thread(cal_src.is_public_https, url):
        await tg.send("Bu link olmadı: https ile başlayan, internetten erişilebilen bir takvim (.ics) adresi olmalı.", to=[chat_id])
        return
    try:
        async with client() as c:
            preview = await cal_src.section(c, url, datetime.now(config.TZ))
    except Exception as e:
        await tg.send(f"Takvimi okuyamadım ({type(e).__name__}). Linkin tamamını kopyaladığından emin ol.", to=[chat_id])
        return
    with db() as con:
        con.execute("INSERT OR REPLACE INTO calendars (chat_id, url, added) VALUES (?, ?, ?)", (chat_id, url, time.time()))
    note = "Linkin geçtiği mesajı sohbetten sildim." if deleted else "Güvenlik için linkin geçtiği mesajı sohbetten silmeni öneririm."
    await tg.send(f"✅ Takvimin bağlandı. Bundan sonra sabah özetinde görünecek.\n{note}\n\nBugün için önizleme:\n{preview}", to=[chat_id])


async def command(chat_id: int, msg: dict, arg: str) -> None:
    arg = arg.strip()
    if arg.lower() in ("sil", "kaldır", "kaldir"):
        with db() as con:
            n = con.execute("DELETE FROM calendars WHERE chat_id = ?", (chat_id,)).rowcount
        await tg.send("🗑 Takvim bağlantın kaldırıldı." if n else "Bağlı bir takvimin yok.", to=[chat_id])
    elif arg:
        await save(chat_id, arg, msg.get("message_id"))
    else:
        awaiting[chat_id] = time.time() + WAIT_SEC
        status = "✅ Şu an bağlı bir takvimin var; yeni link gönderirsen değiştiririm.\n\n" if get(chat_id) else ""
        await tg.send(status + HOWTO, to=[chat_id])
