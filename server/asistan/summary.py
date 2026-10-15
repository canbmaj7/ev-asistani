"""Sabah özeti: herkese kendi sürümü (kendi takvimi; sunucu bölümü yalnızca sahipte)."""
import asyncio
import json
from datetime import datetime, timedelta

from . import calendars, config, home, members, planned
from . import telegram as tg
from .db import kv_get, kv_json, kv_set
from .sources import calendar as cal_src
from .sources import rates, server, weather
from .sources.common import client, tr_date
from .util import log

LABELS = {"hava": "🌤 Hava", "kalite": "🌫 Hava kalitesi", "kur": "💱 Kur", "takvim": "📅 Takvim", "sunucu": "🖥 Sunucu"}
BAD_AIR = ("🟠", "🔴", "🟣")                      # hava kalitesi yalnızca kötüyse yazılır


async def build(chat_id: int) -> str:
    """Bölümler paralel çekilir; biri hata verirse 'alınamadı' yazılır, mesaj yine gider."""
    now = datetime.now(config.TZ)
    st = kv_json("ozet_state", {})                  # kur geçmişi
    cal_url = calendars.get(chat_id)
    async with client() as c:
        jobs = {
            "hava": weather.section(c),
            "kalite": weather.air_quality(c),
            "kur": rates.section(c, st),
            "takvim": cal_src.section(c, cal_url, now) if cal_url else None,
            "sunucu": server.section(c, config.WATCH_URLS) if members.is_owner(chat_id) else None,
        }
        names = [k for k, v in jobs.items() if v is not None]
        results = dict(zip(names, await asyncio.gather(*(jobs[k] for k in names), return_exceptions=True)))
    kv_set("ozet_state", json.dumps(st))

    def part(name: str) -> str | None:
        if name not in results:
            return None
        res = results[name]
        if isinstance(res, Exception):
            return f"{LABELS[name]}: alınamadı ({type(res).__name__})"
        if name == "kalite" and not any(b in res for b in BAD_AIR):
            return None
        if name == "sunucu" and "❌" not in res and "⚠️" not in res:
            return "🖥 Sunucu: her şey yolunda ✅"       # sorun varsa ayrıntı gösterilir
        return res

    weather_block = "\n".join(p for p in (part("hava"), part("kalite")) if p)
    parts = [f"☀️ Günaydın! {tr_date(now)}", weather_block, part("kur"), part("takvim"),
             planned.summary_alert(), home.summary_section(), part("sunucu")]
    return "\n\n".join(p for p in parts if p)


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
