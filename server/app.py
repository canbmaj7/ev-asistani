"""Nöbetçi sunucu servisi.

Pi'den gelen nabızları (heartbeat) izler. Nabız kesilirse Telegram'a haber verir,
geri gelince kesintinin türünü (elektrik / internet) ve süresini bildirir.
"""
import asyncio
import hmac
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
BEAT_TOKEN = os.environ["BEAT_TOKEN"]
TIMEOUT_SEC = int(os.getenv("TIMEOUT_SEC", "120"))
DB_PATH = os.getenv("DB_PATH", "/data/nobetci.db")
TZ = ZoneInfo(os.getenv("TZ", "Europe/Istanbul"))

log = logging.getLogger("nobetci")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # URL'de bot token'ı var, günlüğe yazılmasın

STARTED_AT = time.time()
state = {"last_beat": 0.0, "boot_id": None, "device": None, "down_since": None, "info": {}}
LIVE_TIMEOUT = int(os.getenv("LIVE_TIMEOUT", "15"))   # /durum canlı yanıt bekleme süresi (sn)
WAIT_SEC = 25                                          # Pi'nin uzun bekleme (long-poll) süresi

ping_event = asyncio.Event()       # /durum istendi: Pi'ye "şimdi ölç" sinyali
live_waiters: list = []            # canlı yanıtı bekleyen /durum istekleri
background_tasks: set = set()


# ---------- Veritabanı ----------
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        con.execute(
            """CREATE TABLE IF NOT EXISTS outages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                started REAL NOT NULL,
                ended REAL NOT NULL
            )"""
        )


def kv_set(k: str, v) -> None:
    with db() as con:
        con.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (k, str(v)))


def kv_get(k: str):
    with db() as con:
        row = con.execute("SELECT v FROM kv WHERE k = ?", (k,)).fetchone()
    return row["v"] if row else None


def load_state() -> None:
    state["last_beat"] = float(kv_get("last_beat") or 0)
    state["boot_id"] = kv_get("boot_id")
    state["device"] = kv_get("device")
    ds = kv_get("down_since")
    state["down_since"] = float(ds) if ds else None


# ---------- Yardımcılar ----------
def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y %H:%M:%S")


def fmt_dur(sec: float) -> str:
    sec = int(max(sec, 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} sa {m} dk"
    if m:
        return f"{m} dk {s} sn"
    return f"{s} sn"


async def send(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text})
            r.raise_for_status()
    except Exception as e:  # bildirim hatası servisi düşürmesin
        log.error("Telegram gönderilemedi: %s", e)


def record_outage(kind: str, started: float, ended: float) -> None:
    with db() as con:
        con.execute(
            "INSERT INTO outages (kind, started, ended) VALUES (?, ?, ?)",
            (kind, started, ended),
        )


# ---------- Nabız ----------
class NetEvent(BaseModel):
    dur: float   # kopma süresi (sn)
    ago: float   # kopmanın bitişinden bu yana geçen süre (sn)


class Beat(BaseModel):
    device: str = "pi"
    boot_id: str
    uptime: float = 0
    net_events: list[NetEvent] = []
    live: bool = False          # /durum isteğine verilen canlı yanıt
    info: dict = {}             # sıcaklık, Wi-Fi, gecikme vb.


async def handle_beat(b: Beat) -> None:
    now = time.time()
    prev_beat = state["last_beat"]
    prev_boot = state["boot_id"]
    rebooted = prev_boot is not None and prev_boot != b.boot_id
    # Alarm akışı zaten bu kesintiyi bildirdiyse Pi'nin kendi ölçümünü tekrar yazma
    already_reported = state["down_since"] is not None or rebooted

    if not already_reported:
        for ev in b.net_events:
            ended = now - ev.ago
            started = ended - ev.dur
            record_outage("internet", started, ended)
            await send(
                "🌐 Kısa internet kesintisi oldu\n"
                f"Başlangıç: {fmt_time(started)}\n"
                f"Süre: {fmt_dur(ev.dur)}"
            )

    if state["down_since"] is not None:
        started = state["down_since"]
        kind = "elektrik" if rebooted else "internet"
        record_outage(kind, started, now)
        icon = "⚡ Elektrik kesintisi bitti" if rebooted else "🌐 İnternet kesintisi bitti"
        await send(
            f"✅ {icon}\n"
            f"Başlangıç: {fmt_time(started)}\n"
            f"Süre: {fmt_dur(now - started)}"
        )
    elif rebooted and prev_beat:
        # Alarm eşiğine varmadan biten kısa elektrik kesintisi
        started = prev_beat
        record_outage("elektrik", started, now)
        await send(
            f"⚡ Kısa elektrik kesintisi oldu\n"
            f"Başlangıç: {fmt_time(started)}\n"
            f"Süre: yaklaşık {fmt_dur(now - started)}"
        )

    state.update(last_beat=now, boot_id=b.boot_id, device=b.device, down_since=None)
    if b.info:
        state["info"] = {**b.info, "uptime": b.uptime}
    if b.live:
        for f in live_waiters:
            if not f.done():
                f.set_result(True)
        live_waiters.clear()
    kv_set("last_beat", now)
    kv_set("boot_id", b.boot_id)
    kv_set("device", b.device)
    kv_set("down_since", "")


async def monitor_loop() -> None:
    while True:
        await asyncio.sleep(10)
        # Sunucu yeni açıldıysa yanlış alarm olmasın diye eşik açılıştan itibaren sayılır
        ref = max(state["last_beat"], STARTED_AT)
        if (
            state["last_beat"]
            and state["down_since"] is None
            and time.time() - ref > TIMEOUT_SEC
        ):
            state["down_since"] = state["last_beat"]
            kv_set("down_since", state["down_since"])
            await send(
                "⚠️ Evden nabız alınamıyor\n"
                f"Son sinyal: {fmt_time(state['last_beat'])}\n"
                "Elektrik ya da internet kesilmiş olabilir."
            )


# ---------- Telegram komutları ----------
def fmt_info(info: dict) -> str:
    lines = []
    if "latency_ms" in info:
        lines.append(f"🌐 İnternet: var ({info['latency_ms']} ms)" if info["latency_ms"] is not None else "🌐 İnternet: YOK")
    if info.get("uptime") is not None:
        lines.append(f"⏱ Pi açık kalma süresi: {fmt_dur(info['uptime'])}")
    if "temp" in info:
        lines.append(f"🌡 CPU: {info['temp']}°C · yük {info.get('load', '?')}")
    if "mem_mb" in info:
        lines.append(f"💾 Boş RAM: {info['mem_mb']} MB")
    if info.get("wifi_dbm") is not None:
        lines.append(f"📶 Wi-Fi sinyali: {info['wifi_dbm']} dBm")
    return "\n".join(lines)


async def live_status() -> str:
    """Pi'ye anlık ölçüm yaptırıp sonucu döndürür."""
    if not state["last_beat"]:
        return "Henüz hiç nabız alınmadı."
    ago = time.time() - state["last_beat"]
    if state["down_since"] is not None:
        return (
            f"🔴 Bağlantı yok\nSon sinyal: {fmt_time(state['last_beat'])} "
            f"({fmt_dur(ago)} önce)"
        )
    fut = asyncio.get_running_loop().create_future()
    live_waiters.append(fut)
    ping_event.set()
    t0 = time.time()
    try:
        await asyncio.wait_for(fut, LIVE_TIMEOUT)
    except asyncio.TimeoutError:
        if fut in live_waiters:
            live_waiters.remove(fut)
        ping_event.clear()
        return (
            f"🟠 Pi canlı kontrole {LIVE_TIMEOUT} sn içinde yanıt vermedi.\n"
            f"Son sinyal {fmt_dur(time.time() - state['last_beat'])} önce geldi; "
            "bağlantı yavaş ya da kesilmek üzere olabilir."
        )
    took = time.time() - t0
    return f"🟢 Her şey yolunda (canlı ölçüm, {took:.1f} sn)\n{fmt_info(state['info'])}"


async def reply_status() -> None:
    await send(await live_status())


def history_text() -> str:
    with db() as con:
        rows = con.execute(
            "SELECT kind, started, ended FROM outages ORDER BY id DESC LIMIT 10"
        ).fetchall()
    if not rows:
        return "Kayıtlı kesinti yok."
    lines = []
    for r in rows:
        icon = "⚡" if r["kind"] == "elektrik" else "🌐"
        lines.append(f"{icon} {fmt_time(r['started'])} — {fmt_dur(r['ended'] - r['started'])}")
    return "Son kesintiler:\n" + "\n".join(lines)


async def telegram_loop() -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    offset = 0
    while True:
        try:
            async with httpx.AsyncClient(timeout=40) as c:
                r = await c.get(url, params={"timeout": 30, "offset": offset})
                data = r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if msg.get("chat", {}).get("id") != TELEGRAM_CHAT_ID:
                    continue
                cmd = (msg.get("text") or "").split()[0].split("@")[0] if msg.get("text") else ""
                if cmd in ("/durum", "/start"):
                    t = asyncio.create_task(reply_status())   # polling'i bloklamasın
                    background_tasks.add(t)
                    t.add_done_callback(background_tasks.discard)
                elif cmd in ("/gecmis", "/geçmiş"):
                    await send(history_text())
        except Exception as e:
            log.error("Telegram polling hatası: %s", e)
            await asyncio.sleep(5)


# ---------- Uygulama ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_state()
    tasks = [asyncio.create_task(monitor_loop()), asyncio.create_task(telegram_loop())]
    log.info("Nöbetçi başladı (eşik %s sn)", TIMEOUT_SEC)
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Nöbetçi", lifespan=lifespan)


@app.post("/beat")
async def beat(b: Beat, x_token: str = Header(default="")):
    if not hmac.compare_digest(x_token, BEAT_TOKEN):
        raise HTTPException(status_code=401, detail="yetkisiz")
    await handle_beat(b)
    return {"ok": True}


@app.post("/wait")
async def wait(x_token: str = Header(default="")):
    """Pi'nin uzun bekleme kanalı: /durum istenince hemen {"ping": true} döner."""
    if not hmac.compare_digest(x_token, BEAT_TOKEN):
        raise HTTPException(status_code=401, detail="yetkisiz")
    try:
        await asyncio.wait_for(ping_event.wait(), WAIT_SEC)
    except asyncio.TimeoutError:
        return {"ping": False}
    ping_event.clear()
    return {"ping": True}


@app.get("/health")
async def health():
    return {"ok": True}
