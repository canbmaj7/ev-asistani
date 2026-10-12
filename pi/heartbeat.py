#!/usr/bin/env python3
"""Nöbetçi Pi ajanı.

- Sunucuya düzenli nabız gönderir.
- İnterneti kendisi de izler; kopma süresini bağlantı gelince bildirir.
- Sunucuya uzun bekleme (long-poll) kanalı açar: /durum istendiğinde anında ölçüp yanıtlar.

Yalnızca standart kütüphane.
"""
import json
import os
import socket
import threading
import time
import urllib.request

URL = os.environ["NOBETCI_URL"]          # örn. https://<alan-adı>/beat
WAIT_URL = URL.rsplit("/", 1)[0] + "/wait"
TOKEN = os.environ["NOBETCI_TOKEN"]
BEAT_EVERY = int(os.getenv("NOBETCI_INTERVAL", "30"))
CHECK_EVERY = 10
MIN_EVENT_SEC = 20                       # bundan kısa kopmalar kaydedilmez
DEVICE = os.getenv("NOBETCI_DEVICE", socket.gethostname())
# Bağımsız hedefler: biri açıksa internet var sayılır
TARGETS = [("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443)]

net_events = []        # [{"dur": sn, "_end": monotonic}] — sunucuya henüz iletilmemiş kopmalar
offline_since = None   # internet son kontrolde yoksa kopma başlangıcı (monotonic)
last_latency = None    # son başarılı internet kontrolünün gecikmesi (ms)
lock = threading.Lock()


def read(path: str) -> str:
    with open(path) as f:
        return f.read().strip()


def internet_probe():
    """İnternete ulaşılabiliyorsa gecikmeyi (ms), değilse None döndürür."""
    for host, port in TARGETS:
        t0 = time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=3):
                return round((time.monotonic() - t0) * 1000)
        except OSError:
            continue
    return None


def check_internet() -> None:
    global offline_since, last_latency
    now = time.monotonic()
    lat = internet_probe()
    if lat is not None:
        last_latency = lat
        if offline_since is not None:
            dur = now - offline_since
            if dur >= MIN_EVENT_SEC:
                net_events.append({"dur": round(dur), "_end": now})
            offline_since = None
    elif offline_since is None:
        offline_since = now


def collect_info(fresh: bool) -> dict:
    """Pi'nin anlık durumu. fresh=True ise interneti de yeniden ölçer."""
    global last_latency
    info = {}
    if fresh:
        last_latency = internet_probe()
    info["latency_ms"] = last_latency
    try:
        info["temp"] = round(int(read("/sys/class/thermal/thermal_zone0/temp")) / 1000, 1)
    except Exception:
        pass
    try:
        info["load"] = round(os.getloadavg()[0], 2)
    except Exception:
        pass
    try:
        for line in read("/proc/meminfo").splitlines():
            if line.startswith("MemAvailable:"):
                info["mem_mb"] = int(line.split()[1]) // 1024
    except Exception:
        pass
    try:  # /proc/net/wireless: iface: status link level noise ...
        lines = read("/proc/net/wireless").splitlines()[2:]
        if lines:
            info["wifi_dbm"] = int(float(lines[0].split()[3].rstrip(".")))
    except Exception:
        pass
    return info


def post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Token": TOKEN},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")


def beat(live: bool = False) -> None:
    with lock:
        now = time.monotonic()
        pending = list(net_events)
        post(
            URL,
            {
                "device": DEVICE,
                "boot_id": read("/proc/sys/kernel/random/boot_id"),
                "uptime": float(read("/proc/uptime").split()[0]),
                "net_events": [
                    {"dur": e["dur"], "ago": round(now - e["_end"])} for e in pending
                ],
                "live": live,
                "info": collect_info(fresh=live),
            },
            timeout=10,
        )
        del net_events[: len(pending)]  # yalnızca başarıyla iletilenleri sil


def listen() -> None:
    """Sunucudan "şimdi ölç" isteğini bekler; gelince anında canlı nabız yollar."""
    while True:
        try:
            if post(WAIT_URL, {}, timeout=40).get("ping"):
                beat(live=True)
        except Exception as e:
            print(f"dinleme hatası: {e}", flush=True)
            time.sleep(5)


threading.Thread(target=listen, daemon=True).start()

last_beat = 0.0
while True:
    check_internet()
    if time.monotonic() - last_beat >= BEAT_EVERY:
        try:
            beat()
            last_beat = time.monotonic()
        except Exception as e:  # sunucuya ulaşılamıyorsa sessizce devam et
            print(f"nabız gönderilemedi: {e}", flush=True)
            last_beat = time.monotonic() - BEAT_EVERY + CHECK_EVERY  # 10 sn sonra tekrar dene
    time.sleep(CHECK_EVERY)
