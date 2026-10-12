"""Sabah özeti: herkese kendi sürümü (kendi takvimi; sunucu bölümü yalnızca sahipte)."""
import asyncio
import json
from datetime import datetime, timedelta

from . import calendars, config, home, members
from . import telegram as tg
from .db import kv_get, kv_json, kv_set
from .sources import calendar as cal_src
from .sources import quakes, rates, server, weather
from .sources.common import client, tr_date
from .util import log

LABELS = {"hava": "🌤 Hava", "kur": "💱 Kur", "takvim": "📅 Takvim", "deprem": "🌍 Deprem", "sunucu": "🖥 Sunucu"}


async def build(chat_id: int) -> str:
    """Bölümler paralel çekilir; biri hata verirse 'alınamadı' yazılır, mesaj yine gider."""
    now = datetime.now(config.TZ)
    st = kv_json("ozet_state", {})                  # kur geçmişi
    cal_url = calendars.get(chat_id)
    async with client() as c:
        jobs = {
            "hava": weather.section(c),
            "kur": rates.section(c, st),
            "takvim": cal_src.section(c, cal_url, now) if cal_url else None,
            "deprem": quakes.fetch(c),
            "sunucu": server.section(c, config.WATCH_URLS) if members.is_owner(chat_id) else None,
        }
        names = [k for k, v in jobs.items() if v is not None]
        results = await asyncio.gather(*(jobs[k] for k in names), return_exceptions=True)
    kv_set("ozet_state", json.dumps(st))
    parts = [f"☀️ Günaydın! {tr_date(now)}"]
    for name, res in zip(names, results):
        if isinstance(res, Exception):
            parts.append(f"{LABELS[name]}: alınamadı ({type(res).__name__})")
        else:
            parts.append(quakes.section(res) if name == "deprem" else res)
    parts.append(home.summary_section())
    return "\n\n".join(parts)


def due(now: datetime, last_sent: str | None) -> bool:
    """Bugünün özeti gönderilmeli mi? Geç açılışta en fazla SUMMARY_CATCHUP_H saat telafi eder."""
    hh, mm = map(int, config.SEND_AT.split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return last_sent != now.date().isoformat() and target <= now < target + timedelta(hours=config.SUMMARY_CATCHUP_H)


async def send_to(chat_id: int) -> None:
    try:
        await tg.send(await build(chat_id), to=[chat_id])
    except Exception as e:
        log.error("Özet hazırlanamadı (%s, %s)", chat_id, type(e).__name__)
        await tg.send(f"Özet hazırlanamadı ({type(e).__name__}).", to=[chat_id])


async def loop() -> None:
    while True:
        now = datetime.now(config.TZ)
        if due(now, kv_get("summary_last_sent")):
            kv_set("summary_last_sent", now.date().isoformat())
            for chat_id in members.recipients():
                await send_to(chat_id)
        await asyncio.sleep(20)
