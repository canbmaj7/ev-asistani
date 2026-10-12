"""Anlık deprem uyarısı: AFAD'ı düzenli sorgular, yeni depremi herkese bildirir."""
import asyncio
import json

from . import config
from . import telegram as tg
from .db import kv_get, kv_json, kv_set
from .sources import quakes as afad
from .sources.common import client
from .util import log


async def fetch(hours: float) -> list[dict]:
    async with client(25) as c:
        return await afad.fetch(c, hours=hours)


async def loop() -> None:
    """İlk turda mevcut depremler sessizce kaydedilir; sonra her yeni deprem bildirilir."""
    first = kv_get("seen_quakes") is None
    seen = set(kv_json("seen_quakes", []))
    while True:
        try:
            new = [e for e in reversed(await fetch(6)) if e["eventID"] not in seen]   # eskiden yeniye
            for e in new:
                seen.add(e["eventID"])
                if not first:
                    await tg.send(afad.alert(e))
            if new or first:
                kv_set("seen_quakes", json.dumps(sorted(seen, key=int)[-500:]))
            first = False
        except Exception as e:
            log.error("Deprem sorgusu başarısız (%s)", type(e).__name__)
        await asyncio.sleep(config.QUAKE_POLL_SEC)


async def reply(chat_id: int) -> None:
    try:
        await tg.send(afad.section(await fetch(24)) + "\n🔗 Kaynak: https://deprem.afad.gov.tr/last-earthquakes", to=[chat_id])
    except Exception as e:
        await tg.send(f"Deprem verisi alınamadı ({type(e).__name__}).", to=[chat_id])
