"""Ev Nöbetçisi sunucu servisi.

- Pi'den gelen nabızları izler; kesintiyi (elektrik / internet) ve süresini Telegram'a bildirir.
- Her sabah hava, kur, takvim, deprem, ev ve sunucu özetini gönderir.
- Türkiye'de deprem olunca anında haber verir.
"""
import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

import ozet
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
BEAT_TOKEN = os.environ["BEAT_TOKEN"]
TIMEOUT_SEC = int(os.getenv("TIMEOUT_SEC", "120"))
DB_PATH = os.getenv("DB_PATH", "/data/nobetci.db")
TZ = ZoneInfo(os.getenv("TZ", "Europe/Istanbul"))
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")      # boşsa web paneli kapalı
ONLINE_SEC = 900                                      # cihaz bu süre içinde görüldüyse "çevrimiçi"
SEND_AT = os.getenv("SEND_AT", "08:00")               # sabah özeti saati
SUMMARY_CATCHUP_H = 2                                 # geç açılışta en fazla bu kadar saat telafi
CALENDAR_ICS = os.getenv("CALENDAR_ICS", "")          # Google Takvim gizli iCal adresi (isteğe bağlı)
WATCH_URLS = [u.strip() for u in os.getenv("WATCH_URLS", "").split(",") if u.strip()]
QUAKE_POLL_SEC = int(os.getenv("QUAKE_POLL_SEC", "60"))

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
            """CREATE TABLE IF NOT EXISTS devices (
                mac TEXT PRIMARY KEY,
                ip TEXT, host TEXT,
                name TEXT NOT NULL DEFAULT '',
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL
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


def timeout_sec() -> int:
    """Alarm eşiği (sn). /esik ile değiştirilebilir, yoksa ortam değişkeni."""
    try:
        return int(kv_get("timeout_sec") or TIMEOUT_SEC)
    except ValueError:
        return TIMEOUT_SEC


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


# ---------- Ağ cihazları ----------
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def clean(v, n: int = 60) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", str(v or ""))[:n]


async def handle_devices(devs: list[dict], now: float) -> None:
    """Pi'nin ağ taramasını işler; ilk taramayı başlangıç listesi sayar, sonradan görülenleri bildirir."""
    seen = {}
    for d in devs[:150]:
        mac = clean(d.get("mac")).lower()
        if MAC_RE.match(mac) and mac != "00:00:00:00:00:00":
            seen[mac] = (clean(d.get("ip"), 45), clean(d.get("host")))
    if not seen:
        return
    with db() as con:
        known = {r["mac"] for r in con.execute("SELECT mac FROM devices")}
        first = not known
        new = []
        for mac, (ip, host) in seen.items():
            if mac in known:
                con.execute(
                    "UPDATE devices SET ip = ?, host = COALESCE(NULLIF(?, ''), host), last_seen = ? WHERE mac = ?",
                    (ip, host, now, mac),
                )
            else:
                con.execute(
                    "INSERT INTO devices (mac, ip, host, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
                    (mac, ip, host, now, now),
                )
                new.append((mac, ip, host))
    if first:
        await send(
            f"📋 Ağda {len(seen)} cihaz bulundu ve başlangıç listesine kaydedildi.\n"
            "Bundan sonra listede olmayan yeni bir cihaz bağlanınca haber vereceğim.\n"
            "Liste: /cihazlar · İsim vermek için: /cihaz SON4 isim",
            to=[TELEGRAM_CHAT_ID],
        )
        return
    for mac, ip, host in new:
        await send(
            "🆕 Ağda yeni bir cihaz göründü\n"
            f"IP: {ip or '?'}  ·  Ad: {host or 'bilinmiyor'}\n"
            f"MAC: {mac}\n"
            f"Tanıyorsan isim ver: /cihaz {mac.replace(':', '')[-4:]} isim",
            to=[TELEGRAM_CHAT_ID],
        )


def devices_text() -> str:
    now = time.time()
    with db() as con:
        rows = con.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
    if not rows:
        return "Henüz tarama sonucu yok. (Pi her 5 dakikada bir tarar.)"
    on = sum(1 for r in rows if now - r["last_seen"] < ONLINE_SEC)
    lines = [f"📡 Ağ cihazları ({on} çevrimiçi / {len(rows)} kayıtlı)"]
    for r in rows[:60]:
        dot = "🟢" if now - r["last_seen"] < ONLINE_SEC else "⚪"
        label = r["name"] or r["host"] or "adsız"
        lines.append(f"{dot} {label} · {r['ip'] or '?'} · …{r['mac'].replace(':', '')[-4:]}")
    return "\n".join(lines)


def name_device(arg: str) -> str:
    token, _, name = arg.strip().partition(" ")
    token = token.lower().replace(":", "")
    name = clean(name.strip(), 40)
    if not token or not name:
        return "Kullanım: /cihaz SON4 isim   (SON4: /cihazlar listesindeki …xxxx)"
    with db() as con:
        rows = con.execute("SELECT mac FROM devices WHERE replace(mac, ':', '') LIKE ?", ("%" + token,)).fetchall()
        if len(rows) != 1:
            return "Böyle bir cihaz yok." if not rows else "Birden çok cihaz eşleşti, daha uzun yaz."
        con.execute("UPDATE devices SET name = ? WHERE mac = ?", (name, rows[0]["mac"]))
    return f"✅ Kaydedildi: {name}"


# ---------- İstatistik ----------
def outage_stats(days: int) -> dict:
    now = time.time()
    lo = now - days * 86400
    since = float(kv_get("since") or now)
    span = max(min(now - lo, now - since), 1)
    with db() as con:
        rows = con.execute("SELECT kind, started, ended FROM outages WHERE ended > ?", (lo,)).fetchall()
    out = {"span": span, "total": 0.0, "longest": None, "kinds": {}}
    for r in rows:
        dur = r["ended"] - max(r["started"], lo)
        k = out["kinds"].setdefault(r["kind"], {"n": 0, "sec": 0.0})
        k["n"] += 1
        k["sec"] += dur
        out["total"] += dur
        if out["longest"] is None or dur > out["longest"][0]:
            out["longest"] = (dur, r["started"])
    out["avail"] = max(0.0, 1 - out["total"] / span)
    return out


def report_text(days_list=(7, 30)) -> str:
    since = kv_get("since")
    parts = []
    for days in days_list:
        st = outage_stats(days)
        title = f"📊 Son {days} gün"
        if st["span"] < days * 86400:
            title += f" (kayıt {fmt_dur(st['span'])}lik)" if st["span"] < 3600 else f" (kayıt {st['span'] / 86400:.1f} günlük)"
        lines = [title]
        if not st["kinds"]:
            lines.append("Hiç kesinti olmadı 🎉")
        for kind, icon, label in (("elektrik", "⚡", "Elektrik"), ("internet", "🌐", "İnternet")):
            k = st["kinds"].get(kind)
            if k:
                lines.append(f"{icon} {label}: {k['n']} kesinti · toplam {fmt_dur(k['sec'])}")
        if st["longest"]:
            lines.append(f"⏳ En uzun: {fmt_dur(st['longest'][0])} ({fmt_time(st['longest'][1])[:16]})")
        lines.append("✅ Erişilebilirlik: %" + f"{st['avail'] * 100:.2f}".replace(".", ","))
        parts.append("\n".join(lines))
    if since:
        parts.append(f"Kayıt başlangıcı: {fmt_time(float(since))[:16]}")
    return "\n\n".join(parts)


def daily_downtime(days: int = 30) -> list[dict]:
    """Son N günün günlük kesinti süresi (sn); gece yarısını aşan kesintiler bölünür."""
    today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    first = today - timedelta(days=days - 1)
    buckets = {(first + timedelta(days=i)).strftime("%d.%m"): 0.0 for i in range(days)}
    with db() as con:
        rows = con.execute("SELECT started, ended FROM outages WHERE ended > ?", (first.timestamp(),)).fetchall()
    for r in rows:
        t = datetime.fromtimestamp(max(r["started"], first.timestamp()), TZ)
        end = datetime.fromtimestamp(r["ended"], TZ)
        while t < end:
            nxt = (t.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
            key = t.strftime("%d.%m")
            if key in buckets:
                buckets[key] += (min(nxt, end) - t).total_seconds()
            t = nxt
    return [{"day": k, "sec": round(v)} for k, v in buckets.items()]


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
    devices: list[dict] | None = None   # Pi'nin ağ taraması sonucu (yoksa None)


async def handle_beat(b: Beat) -> None:
    now = time.time()
    prev_beat = state["last_beat"]
    prev_boot = state["boot_id"]
    rebooted = prev_boot is not None and prev_boot != b.boot_id
    boot_time = now - b.uptime if b.uptime else now   # elektriğin geri geldiği an (Pi açılışı)
    if kv_get("since") is None:
        kv_set("since", now)
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
        # Elektrikte süre, Pi'nin açılış süresini içermesin
        ended = min(now, max(boot_time, started)) if rebooted else now
        record_outage(kind, started, ended)
        icon = "⚡ Elektrik kesintisi bitti" if rebooted else "🌐 İnternet kesintisi bitti"
        await send(
            f"✅ {icon}\n"
            f"Başlangıç: {fmt_time(started)}\n"
            f"Süre: {fmt_dur(ended - started)}"
        )
    elif rebooted and prev_beat:
        # Alarm eşiğine varmadan biten kısa elektrik kesintisi
        started = prev_beat
        ended = min(now, max(boot_time, started))
        record_outage("elektrik", started, ended)
        await send(
            f"⚡ Kısa elektrik kesintisi oldu\n"
            f"Başlangıç: {fmt_time(started)}\n"
            f"Süre: yaklaşık {fmt_dur(ended - started)}"
        )

    state.update(last_beat=now, boot_id=b.boot_id, device=b.device, down_since=None)
    if b.info:
        state["info"] = {**b.info, "uptime": b.uptime}
    if b.live:
        for f in live_waiters:
            if not f.done():
                f.set_result(True)
        live_waiters.clear()
    if b.devices is not None:
        await handle_devices(b.devices, now)
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
            and time.time() - ref > timeout_sec()
        ):
            state["down_since"] = state["last_beat"]
            kv_set("down_since", state["down_since"])
            await send(
                "⚠️ Evden nabız alınamıyor\n"
                f"Son sinyal: {fmt_time(state['last_beat'])}\n"
                "Elektrik ya da internet kesilmiş olabilir."
            )


# ---------- Sabah özeti ----------
def kv_json(k: str, default):
    try:
        return json.loads(kv_get(k) or "null") or default
    except json.JSONDecodeError:
        return default


def home_section() -> str:
    """Özetteki 🏠 Ev bölümü: Pi'nin durumu ve son 24 saatin kesintileri."""
    lines = ["🏠 Ev"]
    if not state["last_beat"]:
        lines.append("Pi'den henüz sinyal yok.")
    elif state["down_since"] is not None:
        lines.append(f"🔴 Pi'ye ulaşılamıyor ({fmt_time(state['down_since'])[:16]}'den beri)")
    else:
        info = state["info"]
        bits = ["🟢 Pi çevrimiçi"]
        if info.get("wifi_dbm") is not None:
            bits.append(f"Wi-Fi {info['wifi_dbm']} dBm")
        if info.get("temp") is not None:
            bits.append(f"{info['temp']}°C")
        lines.append(" · ".join(bits))
    st = outage_stats(1)
    if st["kinds"]:
        parts = [f"{'⚡' if k == 'elektrik' else '🌐'} {v['n']} ({fmt_dur(v['sec'])})" for k, v in st["kinds"].items()]
        lines.append("Son 24 saat kesinti: " + ", ".join(parts))
    else:
        lines.append("Son 24 saatte kesinti yok.")
    return "\n".join(lines)


async def summary_text(full: bool) -> str:
    """full=True: sahip için (sunucu dahil); False: üyeler için."""
    st = kv_json("ozet_state", {})
    text = await ozet.build_summary(st, CALENDAR_ICS if full else "", WATCH_URLS,
                                    include_server=full, extra=home_section())
    kv_set("ozet_state", json.dumps(st))  # kur geçmişi
    return text


def summary_due(now: datetime, last_sent: str | None) -> bool:
    hh, mm = map(int, SEND_AT.split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return last_sent != now.date().isoformat() and target <= now < target + timedelta(hours=SUMMARY_CATCHUP_H)


async def summary_loop() -> None:
    while True:
        now = datetime.now(TZ)
        if summary_due(now, kv_get("summary_last_sent")):
            kv_set("summary_last_sent", now.date().isoformat())
            try:
                await send(await summary_text(full=True), to=[TELEGRAM_CHAT_ID])
            except Exception as e:
                log.error("Sabah özeti hazırlanamadı (%s)", type(e).__name__)
        await asyncio.sleep(20)


# ---------- Deprem ----------
async def fetch_quakes(hours: float) -> list[dict]:
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "ev-nobetcisi/1.0"}) as c:
        return await ozet.quakes(c, hours=hours)


async def quake_loop() -> None:
    """Türkiye'de yeni deprem olunca herkese haber verir. İlk turda mevcutlar sessizce kaydedilir."""
    first = kv_get("seen_quakes") is None
    seen = set(kv_json("seen_quakes", []))
    while True:
        try:
            qs = await fetch_quakes(6)
            new = [e for e in reversed(qs) if e["eventID"] not in seen]   # eskiden yeniye
            for e in new:
                seen.add(e["eventID"])
                if not first:
                    await send(ozet.quake_alert(e))
            if new or first:
                kv_set("seen_quakes", json.dumps(sorted(seen, key=int)[-500:]))
            first = False
        except Exception as e:
            log.error("Deprem sorgusu başarısız (%s)", type(e).__name__)
        await asyncio.sleep(QUAKE_POLL_SEC)


async def reply_quakes(chat_id: int) -> None:
    try:
        await send(ozet.quakes_section(await fetch_quakes(24)), to=[chat_id])
    except Exception as e:
        await send(f"Deprem verisi alınamadı ({type(e).__name__}).", to=[chat_id])


async def reply_summary(chat_id: int) -> None:
    try:
        await send(await summary_text(full=chat_id == TELEGRAM_CHAT_ID), to=[chat_id])
    except Exception as e:
        await send(f"Özet hazırlanamadı ({type(e).__name__}).", to=[chat_id])


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


async def live_measure() -> bool:
    """Pi'ye anlık ölçüm yaptırır. Pi zamanında yanıt verdiyse True."""
    fut = asyncio.get_running_loop().create_future()
    live_waiters.append(fut)
    ping_event.set()
    try:
        await asyncio.wait_for(fut, LIVE_TIMEOUT)
        return True
    except asyncio.TimeoutError:
        if fut in live_waiters:
            live_waiters.remove(fut)
        ping_event.clear()
        return False


async def live_status() -> str:
    if not state["last_beat"]:
        return "Henüz hiç nabız alınmadı."
    ago = time.time() - state["last_beat"]
    if state["down_since"] is not None:
        return (
            f"🔴 Bağlantı yok\nSon sinyal: {fmt_time(state['last_beat'])} "
            f"({fmt_dur(ago)} önce)"
        )
    t0 = time.time()
    if not await live_measure():
        return (
            f"🟠 Pi canlı kontrole {LIVE_TIMEOUT} sn içinde yanıt vermedi.\n"
            f"Son sinyal {fmt_dur(time.time() - state['last_beat'])} önce geldi; "
            "bağlantı yavaş ya da kesilmek üzere olabilir."
        )
    return f"🟢 Her şey yolunda (canlı ölçüm, {time.time() - t0:.1f} sn)\n{fmt_info(state['info'])}"


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

HELP_MEMBER = (
    "/durum – evin canlı durumu\n/ozet – günlük özet (hava, kur, deprem)\n/deprem – son 24 saatin depremleri\n"
    "/gecmis – son kesintiler\n/rapor – 7 ve 30 günlük istatistik\n/ayril – bildirimlerden çık\n\n"
    "Türkiye'de deprem olunca otomatik haber veririm."
)
HELP_OWNER = (
    f"/durum – evin canlı durumu\n/ozet – sabah özetini şimdi gönder (her gün {SEND_AT}'de otomatik)\n"
    "/deprem – son 24 saatin depremleri\n/gecmis – son kesintiler\n/rapor [gün] – kesinti istatistiği\n"
    "/esik [dk] – alarm eşiği (şimdi/değiştir)\n/cihazlar – ağdaki cihazlar\n/cihaz SON4 isim – cihaza isim ver\n"
    "/davet – yeni üye için kod üret\n/uyeler – üyeleri listele\n/cikar ID – üyeyi çıkar"
)


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
    elif cmd == "/ozet":
        await send("⏳ Özet hazırlanıyor…", to=[chat_id])
        spawn(reply_summary(chat_id))
    elif cmd == "/deprem":
        spawn(reply_quakes(chat_id))
    elif cmd == "/rapor":
        try:
            days = int(arg.strip())
            await send(report_text((max(1, min(days, 365)),)), to=[chat_id])
        except ValueError:
            await send(report_text(), to=[chat_id])
    elif owner and cmd == "/esik":
        if not arg.strip():
            await send(f"Alarm eşiği şu an {timeout_sec() // 60} dk {timeout_sec() % 60} sn. Değiştirmek için: /esik 3", to=[chat_id])
            return
        try:
            minutes = float(arg.strip().replace(",", "."))
        except ValueError:
            await send("Kullanım: /esik DAKİKA  (örn. /esik 3)", to=[chat_id])
            return
        if not 2 <= minutes <= 60:
            await send("Eşik 2 ile 60 dakika arasında olmalı (Pi 30 sn'de bir nabız yollar).", to=[chat_id])
            return
        kv_set("timeout_sec", int(minutes * 60))
        await send(f"✅ Alarm eşiği {minutes:g} dakika yapıldı.", to=[chat_id])
    elif owner and cmd == "/cihazlar":
        await send(devices_text(), to=[chat_id])
    elif owner and cmd == "/cihaz":
        await send(name_device(arg), to=[chat_id])
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
    {"command": "ozet", "description": "Günlük özet: hava, kur, takvim, deprem"},
    {"command": "deprem", "description": "Son 24 saatin depremleri"},
    {"command": "gecmis", "description": "Son kesintiler"},
    {"command": "rapor", "description": "7 ve 30 günlük kesinti istatistiği"},
    {"command": "yardim", "description": "Komut listesi"},
    {"command": "katil", "description": "Davet koduyla bildirimlere katıl"},
    {"command": "ayril", "description": "Bildirimlerden çık"},
]
OWNER_COMMANDS = [
    c for c in MEMBER_COMMANDS if c["command"] not in ("katil", "ayril")
] + [
    {"command": "esik", "description": "Alarm eşiği: /esik 3 (dakika)"},
    {"command": "cihazlar", "description": "Ağdaki cihazlar"},
    {"command": "cihaz", "description": "Cihaza isim ver: /cihaz SON4 isim"},
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
    tasks = [asyncio.create_task(f()) for f in (monitor_loop, telegram_loop, summary_loop, quake_loop)]
    log.info("Nöbetçi başladı (eşik %s sn, panel %s, özet %s, takvim %s, %d site, deprem M%s)", timeout_sec(),
             "açık" if PANEL_PASSWORD else "kapalı", SEND_AT, "açık" if CALENDAR_ICS else "kapalı",
             len(WATCH_URLS), ozet.QUAKE_MIN_MAG)
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


# ---------- Web paneli ----------
basic = HTTPBasic(auto_error=False)
panel_failures: list[float] = []
PANEL_HTML = Path(__file__).with_name("panel.html")


def panel_auth(c: HTTPBasicCredentials | None = Depends(basic)) -> None:
    if not PANEL_PASSWORD:
        raise HTTPException(status_code=404)
    now = time.time()
    panel_failures[:] = [t for t in panel_failures if now - t < 600]
    if len(panel_failures) >= 10:
        raise HTTPException(status_code=429, detail="çok fazla hatalı deneme")
    if c is None or not hmac.compare_digest(c.password.encode(), PANEL_PASSWORD.encode()):
        if c is not None:
            panel_failures.append(now)
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Nöbetçi"'})


def panel_data() -> dict:
    now = time.time()
    with db() as con:
        outs = [dict(r) for r in con.execute("SELECT kind, started, ended FROM outages ORDER BY id DESC LIMIT 30")]
        devs = [dict(r) for r in con.execute("SELECT mac, ip, host, name, last_seen FROM devices ORDER BY last_seen DESC LIMIT 100")]
        members = con.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    stats = {}
    for d in (7, 30):
        st = outage_stats(d)
        stats[str(d)] = {
            "total": round(st["total"]), "avail": st["avail"],
            "longest": round(st["longest"][0]) if st["longest"] else 0,
            "kinds": {k: {"n": v["n"], "sec": round(v["sec"])} for k, v in st["kinds"].items()},
        }
    return {
        "now": now,
        "status": "down" if state["down_since"] is not None else ("up" if state["last_beat"] else "empty"),
        "last_beat": state["last_beat"], "down_since": state["down_since"],
        "info": state["info"], "timeout_sec": timeout_sec(), "members": members,
        "since": float(kv_get("since") or 0) or None,
        "stats": stats, "daily": daily_downtime(30), "outages": outs,
        "devices": [{**d, "online": now - d["last_seen"] < ONLINE_SEC} for d in devs],
    }


@app.get("/panel", response_class=HTMLResponse, dependencies=[Depends(panel_auth)])
async def panel():
    return HTMLResponse(PANEL_HTML.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/panel/data", dependencies=[Depends(panel_auth)])
async def panel_json():
    return panel_data()


@app.post("/panel/live", dependencies=[Depends(panel_auth)])
async def panel_live():
    if state["down_since"] is None and state["last_beat"]:
        await live_measure()
    return panel_data()


@app.get("/health")
async def health():
    return {"ok": True}
