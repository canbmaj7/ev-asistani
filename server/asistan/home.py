"""Evdeki Pi'nin izlenmesi: nabız, kesinti tespiti (elektrik/internet), canlı ölçüm."""
import asyncio
import time

from pydantic import BaseModel

from . import config, devices, power, speed, stats
from . import telegram as tg
from .db import kv_get, kv_set
from .util import fmt_dur, fmt_time

STARTED_AT = time.time()
FLAKY_WINDOW = 1800     # sn: bu sürede çok sayıda kısa kopma olursa mesajlar tek özete iner
FLAKY_AFTER = 3         # ilk 3 kopma ayrı ayrı bildirilir
RESET_UPTIME_SEC = 75   # yeniden başlayan Pi'nin ilk nabzı bundan erken gelirse router ayaktaydı → elektrik değil, Pi'nin kendi gücü
LAN_NOTE = {True: "\nRouter/Wi-Fi ayaktaydı: sorun hat ya da operatör tarafında.",
            False: "\nRouter'a da ulaşılamadı: Wi-Fi/router tarafında bir kopma."}
LAN_SHORT = {True: "hat/operatör", False: "router/Wi-Fi"}
state = {"last_beat": 0.0, "boot_id": None, "device": None, "down_since": None, "info": {}}
ping_event = asyncio.Event()       # /durum istendi: Pi'ye "şimdi ölç" sinyali (long-poll kanalı)
live_waiters: list = []            # canlı yanıtı bekleyen istekler
speed_waiters: list = []           # hız testi sonucunu bekleyen istekler
pending_cmd: dict = {"cmd": None}  # Pi'ye uzun bekleme kanalıyla iletilecek komut


class NetEvent(BaseModel):
    dur: float   # Pi'nin ölçtüğü internet kopması (sn)
    ago: float   # kopmanın bitişinden bu yana (sn)
    lan: bool | None = None   # kopma boyunca yerel ağ geçidi (router) erişilebilir miydi? (eski ajanda None)


class Beat(BaseModel):
    device: str = "pi"
    boot_id: str
    uptime: float = 0
    net_events: list[NetEvent] = []
    live: bool = False                  # /durum isteğine verilen canlı yanıt
    info: dict = {}                     # sıcaklık, Wi-Fi, gecikme vb.
    devices: list[dict] | None = None   # ağ taraması sonucu (yoksa None)
    speedtest: dict | None = None       # hız testi sonucu (yoksa None)


def muted() -> bool:
    """Bakım modu (Pi güncellenirken): kesinti mesajı gönderilmez, kayıt tutulmaz."""
    return time.time() < float(kv_get("mute_until") or 0)


def timeout_sec() -> int:
    """Alarm eşiği (sn): /esik ile değiştirilebilir."""
    try:
        return int(kv_get("timeout_sec") or config.TIMEOUT_SEC)
    except ValueError:
        return config.TIMEOUT_SEC


def load_state() -> None:
    state["last_beat"] = float(kv_get("last_beat") or 0)
    state["boot_id"] = kv_get("boot_id")
    state["device"] = kv_get("device")
    ds = kv_get("down_since")
    state["down_since"] = float(ds) if ds else None


async def handle_beat(b: Beat) -> None:
    now = time.time()
    prev_beat, prev_boot = state["last_beat"], state["boot_id"]
    rebooted = prev_boot is not None and prev_boot != b.boot_id    # Pi yeniden başlamış → elektrik
    boot_time = now - b.uptime if b.uptime else now                # elektriğin geri geldiği an
    if kv_get("since") is None:
        kv_set("since", now)

    quiet = muted()
    # Pi'nin kendi ölçtüğü kısa internet kopmaları (alarm akışı zaten bildirmediyse)
    if state["down_since"] is None and not rebooted and not quiet:
        for ev in b.net_events:
            ended = now - ev.ago
            started = ended - ev.dur
            recent = stats.recent_count("internet", now - FLAKY_WINDOW)
            stats.record_outage("internet", started, ended, LAN_SHORT.get(ev.lan, ""))
            if recent < FLAKY_AFTER:
                await tg.send(f"🌐 Kısa internet kesintisi oldu\nBaşlangıç: {fmt_time(started)}\nSüre: {fmt_dur(ev.dur)}"
                              + LAN_NOTE.get(ev.lan, ""))
            elif now - float(kv_get("flaky_msg_at") or 0) > FLAKY_WINDOW:
                # Bağlantı dengesiz: her kopma için ayrı mesaj yerine yarım saatte en çok bir özet
                kv_set("flaky_msg_at", now)
                await tg.send(f"🌐 Bağlantı dengesiz: son {FLAKY_WINDOW // 60} dakikada {recent + 1} kısa internet kopması oldu.\n"
                              "Sorun sürerken tek tek bildirmeyeceğim; kayıtlar /gecmis ve panelde.")

    if quiet:
        pass
    elif state["down_since"] is not None:
        started = state["down_since"]
        kind = "elektrik" if rebooted else "internet"
        ended = min(now, max(boot_time, started)) if rebooted else now   # açılış süresi sayılmasın
        stats.record_outage(kind, started, ended)
        title = "⚡ Elektrik kesintisi bitti" if rebooted else "🌐 İnternet kesintisi bitti"
        await tg.send(f"✅ {title}\nBaşlangıç: {fmt_time(started)}\nSüre: {fmt_dur(ended - started)}")
    elif rebooted and prev_beat and b.uptime and b.uptime < RESET_UPTIME_SEC:
        # Pi yeniden başladı ama ağ (router) kesilmedi: evde elektrik var, Pi'nin beslemesi sarsılmış (adaptör/kablo/priz).
        # Elektrik kesintisi sayılmaz, bildirim gitmez; yalnızca /gecmis ve panelde "Pi resetlendi" olarak görünür.
        stats.record_outage("reset", prev_beat, min(now, max(boot_time, prev_beat)), power.reset_note(b.info))
        await power.after_reset(b.info, now)
    elif rebooted and prev_beat:
        # Alarm eşiğine varmadan biten kısa elektrik kesintisi (router da gitmiş: ilk nabız geç geldi)
        started = prev_beat
        ended = min(now, max(boot_time, started))
        stats.record_outage("elektrik", started, ended)
        await tg.send(f"⚡ Kısa elektrik kesintisi oldu\nBaşlangıç: {fmt_time(started)}\n"
                      f"Süre: yaklaşık {fmt_dur(ended - started)}")

    state.update(last_beat=now, boot_id=b.boot_id, device=b.device, down_since=None)
    if b.info:
        state["info"] = {**b.info, "uptime": b.uptime}
        await power.check_info(b.info, b.boot_id, now)
    if b.live:
        for f in live_waiters:
            if not f.done():
                f.set_result(True)
        live_waiters.clear()
    if b.devices is not None:
        await devices.handle_scan(b.devices, now, net=str((b.info or {}).get("ssid") or state["info"].get("ssid") or ""))
    if b.speedtest:
        speed.record(b.speedtest, b.speedtest.get("trigger", "gece"))
        for f in speed_waiters:
            if not f.done():
                f.set_result(b.speedtest)
        speed_waiters.clear()
    kv_set("last_beat", now)
    kv_set("boot_id", b.boot_id)
    kv_set("device", b.device)
    kv_set("down_since", "")


async def monitor_loop() -> None:
    """Nabız eşikten uzun süre gelmezse herkese uyarı gönderir."""
    while True:
        await asyncio.sleep(10)
        ref = max(state["last_beat"], STARTED_AT)   # sunucu yeni açıldıysa yanlış alarm olmasın
        if state["last_beat"] and state["down_since"] is None and time.time() - ref > timeout_sec() and not muted():
            state["down_since"] = state["last_beat"]
            kv_set("down_since", state["down_since"])
            await tg.send(f"⚠️ Evden nabız alınamıyor\nSon sinyal: {fmt_time(state['last_beat'])}\n"
                          "Elektrik ya da internet kesilmiş olabilir.")


async def wait_for_ping() -> dict:
    """Pi'nin uzun bekleme isteği: bir şey istenirse hemen {"ping": True, "cmd": ...} döner."""
    try:
        await asyncio.wait_for(ping_event.wait(), config.WAIT_SEC)
    except asyncio.TimeoutError:
        return {"ping": False}
    ping_event.clear()
    cmd, pending_cmd["cmd"] = pending_cmd["cmd"], None
    return {"ping": True, "cmd": cmd}


async def request_update() -> tuple[dict, dict] | None:
    """Pi ajanına "yeniden başlat, en son imzalı sürümü al" der. Yeniden bağlanınca (önceki, sonraki) bilgiyi döndürür."""
    before = dict(state["info"])
    pending_cmd["cmd"] = "update"
    ping_event.set()
    deadline = time.time() + config.UPDATE_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(1)
        info = state["info"]
        if info.get("agent_start") and info.get("agent_start") != before.get("agent_start") and time.time() - state["last_beat"] < 20:
            return before, dict(info)
    pending_cmd["cmd"] = None
    ping_event.clear()
    return None


async def request_speedtest() -> dict | None:
    """Pi'ye şimdi hız testi yaptırır; sonucu (ya da zaman aşımında None) döndürür."""
    fut = asyncio.get_running_loop().create_future()
    speed_waiters.append(fut)
    pending_cmd["cmd"] = "speedtest"
    ping_event.set()
    try:
        return await asyncio.wait_for(fut, config.SPEEDTEST_TIMEOUT)
    except asyncio.TimeoutError:
        if fut in speed_waiters:
            speed_waiters.remove(fut)
        return None


async def live_measure() -> bool:
    """Pi'ye anlık ölçüm yaptırır; zamanında yanıt verdiyse True."""
    fut = asyncio.get_running_loop().create_future()
    live_waiters.append(fut)
    ping_event.set()
    try:
        await asyncio.wait_for(fut, config.LIVE_TIMEOUT)
        return True
    except asyncio.TimeoutError:
        if fut in live_waiters:
            live_waiters.remove(fut)
        ping_event.clear()
        return False


def fmt_info(info: dict) -> str:
    lines = []
    if "latency_ms" in info:
        lines.append(f"🌐 İnternet: var ({info['latency_ms']} ms)" if info["latency_ms"] is not None else "🌐 İnternet: YOK")
    if info.get("uptime") is not None:
        lines.append(f"⏱ Pi açık kalma süresi: {fmt_dur(info['uptime'])}")
    if info.get("throttled") is not None:
        lines.append(f"🔌 Güç: {power.power_text(info)}")
    if "temp" in info:
        lines.append(f"🌡 CPU: {info['temp']}°C · yük {info.get('load', '?')}")
    if "mem_mb" in info:
        lines.append(f"💾 Boş RAM: {info['mem_mb']} MB")
    if info.get("agent") is not None:
        lines.append(f"🧩 Ajan sürümü: {info['agent']}")
    if info.get("wifi_dbm") is not None or info.get("ssid"):
        net = f" · {info['ssid']}" if info.get("ssid") else ""
        dbm = f"{info['wifi_dbm']} dBm" if info.get("wifi_dbm") is not None else "?"
        lines.append(f"📶 Wi-Fi sinyali: {dbm}{net}" + (f" · {info['ip']}" if info.get("ip") else ""))
    return "\n".join(lines)


async def live_status() -> str:
    if not state["last_beat"]:
        return "Henüz hiç nabız alınmadı."
    if state["down_since"] is not None:
        return (f"🔴 Bağlantı yok\nSon sinyal: {fmt_time(state['last_beat'])} "
                f"({fmt_dur(time.time() - state['last_beat'])} önce)")
    t0 = time.time()
    if not await live_measure():
        return (f"🟠 Pi canlı kontrole {config.LIVE_TIMEOUT} sn içinde yanıt vermedi.\n"
                f"Son sinyal {fmt_dur(time.time() - state['last_beat'])} önce geldi; "
                "bağlantı yavaş ya da kesilmek üzere olabilir.")
    return f"🟢 Her şey yolunda (canlı ölçüm, {time.time() - t0:.1f} sn)\n{fmt_info(state['info'])}"


def summary_section() -> str:
    """Sabah özetindeki 🏠 Ev satırı: sorun yoksa tek satır."""
    if not state["last_beat"]:
        return "🏠 Ev: Pi'den henüz sinyal yok."
    if state["down_since"] is not None:
        return f"🏠 Ev: 🔴 Pi'ye ulaşılamıyor ({fmt_time(state['down_since'])[:16]}'den beri)"
    st = stats.outage_stats(1)
    if st["kinds"]:
        return "🏠 Ev: son 24 saat kesinti: " + ", ".join(
            f"{stats.ICONS.get(k, '🌐')} {v['n']} ({fmt_dur(v['sec'])})" for k, v in st["kinds"].items())
    return "🏠 Ev: her şey yolunda 🟢"