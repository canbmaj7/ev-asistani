"""Kişisel takvim: .ics linkinden o günün etkinlikleri.

Link kullanıcıdan geldiği için yalnızca internetteki https adreslerine (ve yönlendirmelere)
izin verilir; sunucunun iç ağına istek attırılamaz.
"""
import asyncio
import ipaddress
import socket
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx

from .. import config


def is_public_https(url: str) -> bool:
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        return False
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(u.hostname, u.port or 443)}
    except OSError:
        return False
    return bool(addrs) and all(ipaddress.ip_address(a).is_global for a in addrs)


async def fetch_public(c: httpx.AsyncClient, url: str, max_redirects: int = 5) -> httpx.Response:
    """Adresi getirir; her yönlendirme hedefini de ayrıca denetler."""
    for _ in range(max_redirects + 1):
        if not await asyncio.to_thread(is_public_https, url):
            raise ValueError("izin verilmeyen adres")
        r = await c.get(url, follow_redirects=False)
        if not r.is_redirect:
            r.raise_for_status()
            return r
        url = str(r.next_request.url)
    raise ValueError("çok fazla yönlendirme")


async def section(c: httpx.AsyncClient, url: str, day: datetime) -> str:
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical((await fetch_public(c, url)).content)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    items = []
    for ev in recurring_ical_events.of(cal).between(start, start + timedelta(days=1)):
        dt = ev.get("DTSTART").dt
        title = str(ev.get("SUMMARY") or "(başlıksız)")[:80]
        if isinstance(dt, datetime):
            dt = dt.astimezone(config.TZ) if dt.tzinfo else dt.replace(tzinfo=config.TZ)
            items.append((dt.strftime("%H:%M"), title))
        else:
            items.append(("Tüm gün", title))
    items.sort(key=lambda x: (x[0] != "Tüm gün", x[0]))
    if not items:
        return "📅 Bugün\nTakvimde etkinlik yok."
    return "📅 Bugün\n" + "\n".join(f"{t} · {s}" for t, s in items[:12])
