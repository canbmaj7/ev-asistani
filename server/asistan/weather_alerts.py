"""Anlık hava uyarıları (Open-Meteo): yağmur başlıyor, kuvvetli rüzgâr, fırtına, kuvvetli yağış, gece don."""
import asyncio
import time
from datetime import datetime

from . import config
from . import telegram as tg
from .db import kv_get, kv_set
from .sources import weather
from .sources.common import client
from .util import in_quiet_hours, log

SOURCE = "🔗 Kaynak: open-meteo.com"
# kural adı → aynı uyarının tekrar gönderilmeyeceği süre (sn)
COOLDOWN = {"yagmur": 3 * 3600, "ruzgar": 12 * 3600, "firtina": 6 * 3600, "saganak": 6 * 3600, "don": 20 * 3600}


def evaluate(f: dict, now: datetime) -> list[tuple[str, str, bool]]:
    """Tahminden uyarılar: [(kural, mesaj, ciddi_mi)]. Ciddi olanlar sessiz saatlerde de gider."""
    out = []
    rain = f["minutely_15"]["precipitation"]
    if rain and rain[0] < 0.1:
        start = next((i for i, v in enumerate(rain[1:5], 1) if v >= 0.3), None)
        if start:
            out.append(("yagmur", f"🌧 Yaklaşık {start * 15} dakika içinde yağmur başlıyor ({config.CITY}).\nÇamaşırlar dışarıdaysa topla.", False))
    h = f["hourly"]
    gusts = h["wind_gusts_10m"][:6]
    if gusts and max(gusts) >= 60:
        i = gusts.index(max(gusts))
        out.append(("ruzgar", f"💨 Kuvvetli rüzgâr bekleniyor: hamle {round(max(gusts))} km/sa (~{h['time'][i][-5:]}).",
                    max(gusts) >= 80))
    codes = h["weather_code"][:3]
    if any(code in (95, 96, 99) for code in codes):
        out.append(("firtina", "⛈ Önümüzdeki 3 saatte gök gürültülü fırtına bekleniyor.", False))
    rain6 = sum(h["precipitation"][:6])
    if rain6 >= 20:
        out.append(("saganak", f"🌊 Kuvvetli yağış: 6 saatte ~{round(rain6)} mm bekleniyor. Su baskınına dikkat.", True))
    if 18 <= now.hour < 22:
        night_min = min(h["temperature_2m"][:14])
        if night_min <= 0:
            out.append(("don", f"🥶 Bu gece don bekleniyor (en düşük {round(night_min)}°). Yollar buzlanabilir.", False))
    return out


async def loop() -> None:
    while True:
        try:
            async with client() as c:
                f = await weather.forecast(c)
            now = datetime.now(config.TZ)
            for rule, text, severe in evaluate(f, now):
                if in_quiet_hours(now.hour, config.QUIET_HOURS) and not severe:
                    continue
                if time.time() - float(kv_get(f"walert_{rule}") or 0) < COOLDOWN[rule]:
                    continue
                kv_set(f"walert_{rule}", time.time())
                await tg.send(f"{text}\n{SOURCE}")
        except Exception as e:
            log.error("Hava uyarısı kontrolü başarısız (%s)", type(e).__name__)
        await asyncio.sleep(config.WEATHER_POLL_SEC)
