import math
from datetime import datetime

import httpx

from .. import config

GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
         "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def client(timeout: float = 20) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, headers={"User-Agent": config.USER_AGENT})


def tr_num(v: float, nd: int = 2) -> str:
    """1234.5 → '1.234,50'"""
    return f"{v:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def tr_date(d: datetime) -> str:
    return f"{d.day} {AYLAR[d.month - 1]} {GUNLER[d.weekday()]}"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))
