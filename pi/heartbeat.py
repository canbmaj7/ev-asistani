#!/usr/bin/env python3
"""Nöbetçi Pi ajanı: sunucuya düzenli nabız gönderir ve interneti kendisi de izler.

İnternet yokken nabız gidemez; Pi bu süreyi kendi ölçer ve bağlantı gelince
sonraki nabızla sunucuya bildirir. Yalnızca standart kütüphane.
"""
import json
import os
import socket
import time
import urllib.request

URL = os.environ["NOBETCI_URL"]          # örn. https://<alan-adı>/beat
TOKEN = os.environ["NOBETCI_TOKEN"]
BEAT_EVERY = int(os.getenv("NOBETCI_INTERVAL", "30"))
CHECK_EVERY = 10
MIN_EVENT_SEC = 20                       # bundan kısa kopmalar kaydedilmez
DEVICE = os.getenv("NOBETCI_DEVICE", socket.gethostname())
# Bağımsız hedefler: biri açıksa internet var sayılır
TARGETS = [("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443)]

net_events = []        # [{"dur": sn, "ago": sn}] — sunucuya henüz iletilmemiş kopmalar
offline_since = None   # internet son kontrolde yoksa kopma başlangıcı (monotonic)


def read(path: str) -> str:
    with open(path) as f:
        return f.read().strip()


def internet_ok() -> bool:
    for host, port in TARGETS:
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            continue
    return False


def check_internet() -> None:
    global offline_since
    now = time.monotonic()
    if internet_ok():
        if offline_since is not None:
            dur = now - offline_since
            if dur >= MIN_EVENT_SEC:
                net_events.append({"dur": round(dur), "ago": 0, "_end": now})
            offline_since = None
    elif offline_since is None:
        offline_since = now


def beat() -> None:
    now = time.monotonic()
    pending = list(net_events)
    body = json.dumps(
        {
            "device": DEVICE,
            "boot_id": read("/proc/sys/kernel/random/boot_id"),
            "uptime": float(read("/proc/uptime").split()[0]),
            "net_events": [
                {"dur": e["dur"], "ago": round(now - e["_end"])} for e in pending
            ],
        }
    ).encode()
    req = urllib.request.Request(
        URL, data=body, headers={"Content-Type": "application/json", "X-Token": TOKEN}
    )
    urllib.request.urlopen(req, timeout=10).read()
    del net_events[: len(pending)]  # yalnızca başarıyla iletilenleri sil


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
