"""Deprem: AFAD. Tarihler UTC gelir, yerel saate çevrilir."""
import re
from datetime import datetime, timedelta, timezone

import httpx

from .. import config
from .common import distance_km, tr_num

AFAD_URL = "https://servisnet.afad.gov.tr/apigateway/deprem/apiv2/event/filter"


def offshore_km(location: str) -> float:
    """AFAD konum metnindeki '[165.21 km]' gibi kıyı uzaklığı (yoksa 0)."""
    m = re.search(r"\[(\d+(?:\.\d+)?) km\]", location or "")
    return float(m.group(1)) if m else 0.0


def in_turkey(e: dict) -> bool:
    """AFAD uzak deniz depremlerini (ör. Girit açıkları) de 'Türkiye' sayar; onları ele."""
    return e.get("country") == "Türkiye" and offshore_km(e.get("location", "")) <= config.QUAKE_MAX_OFFSHORE_KM


async def fetch(c: httpx.AsyncClient, hours: float = 24, min_mag: float | None = None) -> list[dict]:
    min_mag = config.QUAKE_MIN_MAG if min_mag is None else min_mag
    end = datetime.now(timezone.utc)
    r = await c.get(AFAD_URL, params={
        "start": (end - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmag": min_mag, "orderby": "timedesc", "limit": 100, "format": "json",
    }, follow_redirects=True)
    r.raise_for_status()
    out = []
    for e in r.json():
        try:
            e["mag"] = float(e["magnitude"])
            e["lat"], e["lon"] = float(e["latitude"]), float(e["longitude"])
            e["when"] = datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc).astimezone(config.TZ)
            e["dist"] = distance_km(config.LAT, config.LON, e["lat"], e["lon"])
        except (KeyError, ValueError, TypeError):
            continue
        if e["mag"] >= min_mag and in_turkey(e):
            out.append(e)
    return out


def place(e: dict) -> str:
    return re.sub(r"\s*-?\s*\[\d+(?:\.\d+)? km\]\s*", " ", e.get("location", "")).strip() or e.get("province") or "?"


def alert(e: dict) -> str:
    """Anlık deprem bildirimi."""
    icon = "🆘" if e["mag"] >= 6 else "🔴" if e["mag"] >= 5 else "🟠"
    lines = [
        f"{icon} Deprem · M{e['mag']:.1f}",
        f"📍 {place(e)}",
        f"🕐 {e['when'].strftime('%d.%m %H:%M:%S')} · derinlik {tr_num(float(e.get('depth') or 0), 1)} km",
        f"📏 {config.CITY}'ye ~{round(e['dist'])} km",
        f"🗺 https://maps.google.com/?q={e['lat']},{e['lon']}",
        f"🔗 Kaynak: https://deprem.afad.gov.tr/event-detail/{e.get('eventID', '')}",
    ]
    if e["dist"] < 100:
        lines.insert(1, f"⚠️ {config.CITY}'ye yakın!")
    return "\n".join(lines)


def section(qs: list[dict]) -> str:
    """Özetteki ve /deprem'deki liste."""
    title = f"🌍 Deprem (son 24 sa, M≥{config.QUAKE_MIN_MAG:g})"
    if not qs:
        return f"{title}\nYok 🙏"
    lines = [title] + [f"{e['when'].strftime('%H:%M')} M{e['mag']:.1f} {place(e)} · {round(e['dist'])} km" for e in qs[:8]]
    if len(qs) > 8:
        lines.append(f"… ve {len(qs) - 8} deprem daha")
    return "\n".join(lines)
