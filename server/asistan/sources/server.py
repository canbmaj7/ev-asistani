"""Sunucu durumu: konteynerin gördüğü ana makine bilgileri + site/SSL kontrolleri."""
import asyncio
import os
import shutil
import socket
import ssl
import time
from urllib.parse import urlparse

import httpx

from .common import tr_num


def host_stats() -> str:
    mem = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0])
    ram = 100 * (1 - mem["MemAvailable"] / mem["MemTotal"])
    du = shutil.disk_usage("/data" if os.path.isdir("/data") else "/")
    with open("/proc/uptime") as f:
        up_days = float(f.read().split()[0]) / 86400
    return (f"RAM %{round(ram)} · Disk %{round(100 * du.used / du.total)} "
            f"· Yük {tr_num(os.getloadavg()[0])} ({os.cpu_count()} çekirdek) · {up_days:.0f} gündür açık")


def tls_days_left(host: str, port: int = 443) -> int | None:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=6) as s, ctx.wrap_socket(s, server_hostname=host) as t:
            exp = ssl.cert_time_to_seconds(t.getpeercert()["notAfter"])
        return int((exp - time.time()) // 86400)
    except Exception:
        return None


async def check_site(c: httpx.AsyncClient, url: str) -> str:
    host = urlparse(url).hostname or url
    t0 = time.monotonic()
    try:
        r = await c.get(url, follow_redirects=True, timeout=10)
    except Exception as e:
        return f"❌ {host} · {type(e).__name__}"
    line = f"{'✅' if r.status_code < 500 else '❌'} {host} · {r.status_code} · {round((time.monotonic() - t0) * 1000)} ms"
    if url.startswith("https://"):
        days = await asyncio.to_thread(tls_days_left, host)
        if days is not None:
            line += f" · SSL {days} gün" + (" ⚠️" if days < 14 else "")
    return line


async def section(c: httpx.AsyncClient, urls: list[str]) -> str:
    lines = ["🖥 Sunucu", host_stats()]
    if urls:
        lines += await asyncio.gather(*(check_site(c, u) for u in urls))
    return "\n".join(lines)
