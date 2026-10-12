"""Ezan vakitleri (Diyanet) ve Ramazan'da isteğe bağlı sahur/iftar hatırlatması."""
import asyncio
import json
from datetime import date, datetime, timedelta

from . import config, members
from . import telegram as tg
from .db import kv_get, kv_json, kv_set, pref_get, pref_set
from .sources import prayer as src
from .sources.common import client
from .util import log

SOURCE = "🔗 Kaynak: Diyanet (ezanvakti.emushaf.net)"
SAHUR_BEFORE, IFTAR_BEFORE = 45, 15   # dakika


async def days() -> list[dict]:
    """~1 aylık vakitler; bugün yoksa yeniden indirilir."""
    cache = kv_json("prayer_cache", [])
    if not src.for_day(cache, datetime.now(config.TZ).date()):
        async with client() as c:
            cache = await src.month(c)
        kv_set("prayer_cache", json.dumps(cache))
    return cache


async def today_line() -> str:
    d = src.for_day(await days(), datetime.now(config.TZ).date())
    if not d:
        raise ValueError("bugünün vakitleri yok")
    return src.line(d)


def next_prayer(day: dict, now: datetime) -> tuple[str, str, int] | None:
    for key, tr in src.NAMES:
        hh, mm = map(int, day[key].split(":"))
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t > now:
            return tr, day[key], int((t - now).total_seconds() // 60)
    return None


async def reply(chat_id: int) -> None:
    try:
        now = datetime.now(config.TZ)
        d = src.for_day(await days(), now.date())
        nxt = next_prayer(d, now)
        extra = f"\n⏳ {nxt[0]} vaktine {nxt[2] // 60} sa {nxt[2] % 60} dk ({nxt[1]})" if nxt else ""
        await tg.send(f"{src.line(d)}{extra}\n📿 {d['HicriTarihUzun']}\n{SOURCE}", to=[chat_id])
    except Exception as e:
        await tg.send(f"Ezan vakitleri alınamadı ({type(e).__name__}).", to=[chat_id])


async def toggle_ramadan(chat_id: int) -> None:
    on = pref_get(chat_id, "ramazan") != "1"
    pref_set(chat_id, "ramazan", "1" if on else "0")
    await tg.send(f"🌙 Ramazan hatırlatmaları açıldı: sahurdan {SAHUR_BEFORE} dk, iftardan {IFTAR_BEFORE} dk önce haber veririm "
                  "(yalnızca Ramazan ayında)." if on else "🌙 Ramazan hatırlatmaları kapatıldı.", to=[chat_id])


def due_reminders(day: dict, now: datetime) -> list[tuple[str, str]]:
    """Şu an gönderilmesi gereken hatırlatmalar: [(anahtar, mesaj)]."""
    out = []
    for key, before, text in (("Imsak", SAHUR_BEFORE, "🌙 Sahura {m} dk kaldı (İmsak {t})."),
                              ("Aksam", IFTAR_BEFORE, "🍽 İftara {m} dk kaldı (Akşam {t}).")):
        hh, mm = map(int, day[key].split(":"))
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t - timedelta(minutes=before) <= now < t:
            out.append((f"{key}-{now.date().isoformat()}", text.format(m=before, t=day[key])))
    return out


async def ramadan_loop() -> None:
    while True:
        try:
            now = datetime.now(config.TZ)
            d = src.for_day(await days(), now.date())
            if d and src.is_ramadan(d):
                for key, text in due_reminders(d, now):
                    if kv_get(f"ramazan_{key}"):
                        continue
                    kv_set(f"ramazan_{key}", "1")
                    targets = [c for c in members.recipients() if pref_get(c, "ramazan") == "1"]
                    if targets:
                        await tg.send(text, to=targets)
        except Exception as e:
            log.error("Ramazan hatırlatması başarısız (%s)", type(e).__name__)
        await asyncio.sleep(30)
