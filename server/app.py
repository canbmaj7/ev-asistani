"""Nöbetçi sunucu servisi.

Pi'den gelen nabızları (heartbeat) izler. Nabız kesilirse Telegram'a haber verir,
geri gelince kesintinin türünü (elektrik / internet) ve süresini bildirir.
"""
import asyncio
import hmac
import logging
import os
import secrets
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
            """CREATE TABLE IF NOT EXISTS members (
                chat_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                added REAL NOT NULL
            )"""
        )
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


def recipients() -> list[int]:
    """Bildirim alanlar: sahip + davetle katılan üyeler."""
    with db() as con:
        rows = con.execute("SELECT chat_id FROM members").fetchall()
    return [TELEGRAM_CHAT_ID] + [r["chat_id"] for r in rows if r["chat_id"] != TELEGRAM_CHAT_ID]


def is_member(chat_id: int) -> bool:
    return chat_id in recipients()


async def send(text: str, to: list[int] | None = None) -> None:
    """Mesajı `to` listesine, verilmezse tüm alıcılara gönderir."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in to if to is not None else recipients():
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(url, json={"chat_id": chat_id, "text": text})
                r.raise_for_status()
            log.info("Telegram'a gönderildi (%s): %s", chat_id, text.replace("\n", " | "))
        except Exception as e:  # bildirim hatası servisi düşürmesin
            # str(e) bot token'ı içeren URL'yi barındırabilir; yalnızca hata türünü yaz
            log.error("Telegram gönderilemedi (%s, %s)", chat_id, type(e).__name__)


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


async def reply_status(chat_id: int) -> None:
    await send(await live_status(), to=[chat_id])


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


INVITE_TTL = 600
invites: dict[str, float] = {}        # davet kodu -> son geçerlilik zamanı
bad_attempts: list[float] = []        # hatalı /katil denemeleri (kaba kuvveti sınırlamak için)

HELP_MEMBER = "/durum – canlı durum\n/gecmis – son kesintiler\n/ayril – bildirimlerden çık"
HELP_OWNER = HELP_MEMBER + "\n/davet – yeni üye için kod üret\n/uyeler – üyeleri listele\n/cikar ID – üyeyi çıkar"


def spawn(coro) -> None:
    t = asyncio.create_task(coro)      # polling'i bloklamasın
    background_tasks.add(t)
    t.add_done_callback(background_tasks.discard)


def member_name(msg: dict) -> str:
    u = msg.get("from", {})
    return (u.get("first_name") or u.get("username") or "Üye")[:40]


async def handle_join(chat_id: int, msg: dict, arg: str) -> None:
    now = time.time()
    bad_attempts[:] = [t for t in bad_attempts if now - t < INVITE_TTL]
    if len(bad_attempts) >= 10:
        return                                         # çok fazla hatalı deneme: sessizce yoksay
    if is_member(chat_id):
        await send("Zaten üyesin. Komutlar için /yardim", to=[chat_id])
        return
    code = arg.strip().upper()
    if invites.get(code, 0) < now:
        bad_attempts.append(now)
        await send("Kod geçersiz ya da süresi dolmuş.", to=[chat_id])
        return
    del invites[code]                                  # tek kullanımlık
    name = member_name(msg)
    with db() as con:
        con.execute(
            "INSERT OR REPLACE INTO members (chat_id, name, added) VALUES (?, ?, ?)",
            (chat_id, name, now),
        )
    await send(f"✅ Katıldın! Artık elektrik/internet kesintilerinden haberdar olacaksın.\n\n{HELP_MEMBER}", to=[chat_id])
    await send(f"👤 {name} bildirimlere katıldı.", to=[TELEGRAM_CHAT_ID])


async def handle_command(chat_id: int, msg: dict, cmd: str, arg: str) -> None:
    owner = chat_id == TELEGRAM_CHAT_ID
    if cmd == "/katil":
        await handle_join(chat_id, msg, arg)
        return
    if not is_member(chat_id):
        await send("Bu bot özeldir. Davet kodun varsa: /katil KOD", to=[chat_id])
        return
    if cmd in ("/durum", "/start"):
        spawn(reply_status(chat_id))
    elif cmd in ("/gecmis", "/geçmiş"):
        await send(history_text(), to=[chat_id])
    elif cmd in ("/yardim", "/yardım", "/help"):
        await send(HELP_OWNER if owner else HELP_MEMBER, to=[chat_id])
    elif cmd == "/ayril" and not owner:
        with db() as con:
            con.execute("DELETE FROM members WHERE chat_id = ?", (chat_id,))
        await send("Bildirimlerden çıktın. Tekrar katılmak için yeni bir davet kodu gerekir.", to=[chat_id])
        await send(f"👤 {member_name(msg)} bildirimlerden ayrıldı.", to=[TELEGRAM_CHAT_ID])
    elif owner and cmd == "/davet":
        now = time.time()
        for k in [k for k, v in invites.items() if v < now]:
            del invites[k]
        code = secrets.token_hex(3).upper()
        invites[code] = now + INVITE_TTL
        await send(
            "Katılacak kişiye şunu ilet:\n"
            "1) Telegram'da @evnobetcisi_bot'u aç\n"
            f"2) Şunu yaz: /katil {code}\n\n"
            f"Kod {INVITE_TTL // 60} dakika geçerli ve tek kullanımlık.",
            to=[chat_id],
        )
    elif owner and cmd == "/uyeler":
        with db() as con:
            rows = con.execute("SELECT chat_id, name FROM members ORDER BY added").fetchall()
        lines = ["👑 Sen (sahip)"] + [f"👤 {r['name']} — ID {r['chat_id']}" for r in rows]
        await send("\n".join(lines), to=[chat_id])
    elif owner and cmd == "/cikar":
        try:
            target = int(arg.strip())
        except ValueError:
            await send("Kullanım: /cikar ID (ID'yi /uyeler gösterir)", to=[chat_id])
            return
        with db() as con:
            n = con.execute("DELETE FROM members WHERE chat_id = ?", (target,)).rowcount
        await send("Üye çıkarıldı." if n else "Böyle bir üye yok.", to=[chat_id])


MEMBER_COMMANDS = [
    {"command": "durum", "description": "Canlı durum: internet, sıcaklık, Wi-Fi"},
    {"command": "gecmis", "description": "Son kesintiler"},
    {"command": "yardim", "description": "Komut listesi"},
    {"command": "katil", "description": "Davet koduyla bildirimlere katıl"},
    {"command": "ayril", "description": "Bildirimlerden çık"},
]
OWNER_COMMANDS = [
    c for c in MEMBER_COMMANDS if c["command"] not in ("katil", "ayril")
] + [
    {"command": "davet", "description": "Yeni üye için davet kodu üret"},
    {"command": "uyeler", "description": "Üyeleri listele"},
    {"command": "cikar", "description": "Üyeyi çıkar: /cikar ID"},
]


async def set_commands() -> None:
    """`/` yazınca çıkan komut menüsü: üyeler ve sahip için ayrı liste."""
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyCommands"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(base, json={"commands": MEMBER_COMMANDS})
            await c.post(base, json={
                "commands": OWNER_COMMANDS,
                "scope": {"type": "chat", "chat_id": TELEGRAM_CHAT_ID},
            })
    except Exception as e:
        log.error("Komut menüsü ayarlanamadı (%s)", type(e).__name__)


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
                chat = msg.get("chat", {})
                text = (msg.get("text") or "").strip()
                if chat.get("type") != "private" or not text.startswith("/"):
                    continue
                head, _, arg = text.partition(" ")
                await handle_command(chat["id"], msg, head.split("@")[0].lower(), arg)
        except Exception as e:
            # str(e) bot token'ı içeren URL'yi barındırabilir; yalnızca hata türünü yaz
            log.error("Telegram polling hatası (%s)", type(e).__name__)
            await asyncio.sleep(5)


# ---------- Uygulama ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_state()
    await set_commands()
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
