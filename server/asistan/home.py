"""Evdeki Pi'nin izlenmesi: nabız, kesinti tespiti (elektrik/internet), canlı ölçüm."""
import asyncio
import time

from pydantic import BaseModel

from . import config, devices, stats
from . import telegram as tg
from .db import kv_get, kv_set
from .util import fmt_dur, fmt_time

STARTED_AT = time.time()
state = {"last_beat": 0.0, "boot_id": None, "device": None, "down_since": None, "info": {}}
ping_event = asyncio.Event()       # /durum istendi: Pi'ye "şimdi ölç" sinyali (long-poll kanalı)
live_waiters: list = []            # canlı yanıtı bekleyen istekler


class NetEvent(BaseModel):
    dur: float   # Pi'nin ölçtüğü internet kopması (sn)
    ago: float   # kopmanın bitişinden bu yana (sn)


class Beat(BaseModel):
    device: str = "pi"
    boot_id: str
    uptime: float = 0
    net_events: list[NetEvent] = []
    live: bool = False                  # /durum isteğine verilen canlı yanıt
    info: dict = {}                     # sıcaklık, Wi-Fi, gecikme vb.
    devices: list[dict] | None = None   # ağ taraması sonucu (yoksa None)


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

    # Pi'nin kendi ölçtüğü kısa internet kopmaları (alarm akışı zaten bildirmediyse)
    if state["down_since"] is None and not rebooted:
        for ev in b.net_events:
            ended = now - ev.ago
            started = ended - ev.dur
            stats.record_outage("internet", started, ended)
            await tg.send(f"🌐 Kısa internet kesintisi oldu\nBaşlangıç: {fmt_time(started)}\nSüre: {fmt_dur(ev.dur)}")

    if state["down_since"] is not None:
        started = state["down_since"]
        kind = "elektrik" if rebooted else "internet"
        ended = min(now, max(boot_time, started)) if rebooted else now   # açılış süresi sayılmasın
        stats.record_outage(kind, started, ended)
        title = "⚡ Elektrik kesintisi bitti" if rebooted else "🌐 İnternet kesintisi bitti"
        await tg.send(f"✅ {title}\nBaşlangıç: {fmt_time(started)}\nSüre: {fmt_dur(ended - started)}")
    elif rebooted and prev_beat:
        # Alarm eşiğine varmadan biten kısa elektrik kesintisi
        started = prev_beat
        ended = min(now, max(boot_time, started))
        stats.record_outage("elektrik", started, ended)
        await tg.send(f"⚡ Kısa elektrik kesintisi oldu\nBaşlangıç: {fmt_time(started)}\n"
                      f"Süre: yaklaşık {fmt_dur(ended - started)}")

    state.update(last_beat=now, boot_id=b.boot_id, device=b.device, down_since=None)
    if b.info:
        state["info"] = {**b.info, "uptime": b.uptime}
    if b.live:
        for f in live_waiters:
            if not f.done():
                f.set_result(True)
        live_waiters.clear()
    if b.devices is not None:
        await devices.handle_scan(b.devices, now)
    kv_set("last_beat", now)
    kv_set("boot_id", b.boot_id)
    kv_set("device", b.device)
    kv_set("down_since", "")


async def monitor_loop() -> None:
    """Nabız eşikten uzun süre gelmezse herkese uyarı gönderir."""
    while True:
        await asyncio.sleep(10)
        ref = max(state["last_beat"], STARTED_AT)   # sunucu yeni açıldıysa yanlış alarm olmasın
        if state["last_beat"] and state["down_since"] is None and time.time() - ref > timeout_sec():
            state["down_since"] = state["last_beat"]
            kv_set("down_since", state["down_since"])
            await tg.send(f"⚠️ Evden nabız alınamıyor\nSon sinyal: {fmt_time(state['last_beat'])}\n"
                          "Elektrik ya da internet kesilmiş olabilir.")


async def wait_for_ping() -> bool:
    """Pi'nin uzun bekleme isteği: /durum istenirse hemen True döner."""
    try:
        await asyncio.wait_for(ping_event.wait(), config.WAIT_SEC)
    except asyncio.TimeoutError:
        return False
    ping_event.clear()
    return True


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
    if "temp" in info:
        lines.append(f"🌡 CPU: {info['temp']}°C · yük {info.get('load', '?')}")
    if "mem_mb" in info:
        lines.append(f"💾 Boş RAM: {info['mem_mb']} MB")
    if info.get("wifi_dbm") is not None:
        lines.append(f"📶 Wi-Fi sinyali: {info['wifi_dbm']} dBm")
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
    """Sabah özetindeki 🏠 Ev bölümü."""
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
    st = stats.outage_stats(1)
    if st["kinds"]:
        lines.append("Son 24 saat kesinti: " + ", ".join(
            f"{'⚡' if k == 'elektrik' else '🌐'} {v['n']} ({fmt_dur(v['sec'])})" for k, v in st["kinds"].items()))
    else:
        lines.append("Son 24 saatte kesinti yok.")
    return "\n".join(lines)
