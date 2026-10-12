"""Ezan vakitleri: Diyanet verisi (ezanvakti.emushaf.net, ilçe bazında, ~1 aylık)."""
from datetime import date

import httpx

from .. import config

API = "https://ezanvakti.emushaf.net/vakitler/{}"
NAMES = [("Imsak", "İmsak"), ("Gunes", "Güneş"), ("Ogle", "Öğle"), ("Ikindi", "İkindi"), ("Aksam", "Akşam"), ("Yatsi", "Yatsı")]


async def month(c: httpx.AsyncClient) -> list[dict]:
    r = await c.get(API.format(config.PRAYER_DISTRICT_ID))
    r.raise_for_status()
    return r.json()


def for_day(days: list[dict], d: date) -> dict | None:
    key = d.strftime("%d.%m.%Y")
    return next((x for x in days if x.get("MiladiTarihKisa") == key), None)


def line(day: dict) -> str:
    return "🕌 " + " · ".join(f"{tr} {day[k]}" for k, tr in NAMES)


def is_ramadan(day: dict) -> bool:
    return "ramazan" in (day.get("HicriTarihUzun") or "").lower()
