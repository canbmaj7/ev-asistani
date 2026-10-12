"""Uygulama girişi: FastAPI, arka plan döngüleri ve Pi uç noktaları (/beat, /wait)."""
import asyncio
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException

from . import commands, config, home, panel, quakes, summary
from .db import init_db
from .util import log

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)   # URL'de bot token'ı var, günlüğe yazılmasın


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    home.load_state()
    tasks = [asyncio.create_task(f()) for f in (home.monitor_loop, commands.loop, summary.loop, quakes.loop)]
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
    """Pi'nin uzun bekleme kanalı: /durum istenince hemen {"ping": true} döner."""
    check_token(x_token)
    return {"ping": await home.wait_for_ping()}


@app.get("/health")
async def health():
    return {"ok": True}
