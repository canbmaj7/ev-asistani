"""Uygulama girişi: FastAPI, arka plan döngüleri ve Pi uç noktaları (/beat, /wait)."""
import asyncio
import hmac
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import commands, config, home, panel, planned, prayer, quakes, summary, weather_alerts
from .db import init_db, kv_set
from .util import log

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)   # URL'de bot token'ı var, günlüğe yazılmasın


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    home.load_state()
    loops = (home.monitor_loop, commands.loop, summary.loop, quakes.loop,
             planned.loop, weather_alerts.loop, prayer.ramadan_loop)
    tasks = [asyncio.create_task(f()) for f in loops]
    log.info("Ev Asistanı başladı (eşik %s sn, panel %s, özet %s, %d site, deprem M%s)",
             home.timeout_sec(), "açık" if config.PANEL_PASSWORD else "kapalı", config.SEND_AT,
             len(config.WATCH_URLS), config.QUAKE_MIN_MAG)
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Ev Asistanı", lifespan=lifespan)
app.include_router(panel.router)


def check_token(x_token: str) -> None:
    if not hmac.compare_digest(x_token, config.BEAT_TOKEN):
        raise HTTPException(status_code=401, detail="yetkisiz")


@app.post("/beat")
async def beat(b: home.Beat, x_token: str = Header(default="")):
    check_token(x_token)
    await home.handle_beat(b)
    return {"ok": True}


@app.post("/wait")
async def wait(x_token: str = Header(default="")):
    """Pi'nin uzun bekleme kanalı: /durum veya hız testi istenince hemen döner."""
    check_token(x_token)
    return await home.wait_for_ping()


@app.post("/pi/update")
async def pi_update(x_token: str = Header(default="")):
    """Pi ajanını güncellemeye zorlar (betikten): komutu kanala koyar, beklemeden döner."""
    check_token(x_token)
    home.pending_cmd["cmd"] = "update"
    home.ping_event.set()
    return {"ok": True}


class Maintenance(BaseModel):
    minutes: float = 15


@app.post("/maintenance")
async def maintenance(m: Maintenance, x_token: str = Header(default="")):
    """Bakım modu: Pi yeniden başlatılırken kesinti mesajlarını susturur (en fazla 2 saat)."""
    check_token(x_token)
    until = time.time() + max(0.0, min(m.minutes, 120)) * 60
    kv_set("mute_until", until)
    return {"ok": True, "mute_until": until}


@app.get("/health")
async def health():
    return {"ok": True}
