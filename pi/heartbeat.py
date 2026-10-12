#!/usr/bin/env python3
"""Nöbetçi Pi ajanı.

- Sunucuya düzenli nabız gönderir.
- İnterneti kendisi de izler; kopma süresini bağlantı gelince bildirir.
- Sunucuya uzun bekleme (long-poll) kanalı açar: /durum istendiğinde anında ölçüp yanıtlar.
- Wi-Fi koparsa yeniden bağlanmayı dener.
- Sunucu çökerse (Pi ve internet sağlamken) Telegram'dan doğrudan haber verir.
- Ağdaki cihazları periyodik tarar, sonucu sunucuya iletir.
- systemd watchdog'a "yaşıyorum" der; ajan donarsa systemd yeniden başlatır.

Yalnızca standart kütüphane.
"""
import concurrent.futures
import ipaddress
import json
import os
import socket
import subprocess
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

# Wi-Fi yeniden bağlanma
RECONNECT_AFTER = 180                    # internet bu kadar sn yoksa dene
RECONNECT_COOLDOWN = 600                 # iki deneme arası en az

# Sunucu izleme: Telegram'a doğrudan yazmak için (isteğe bağlı)
TG_TOKEN = os.getenv("NOBETCI_TG_TOKEN", "")
TG_CHAT = os.getenv("NOBETCI_TG_CHAT", "")
SERVER_ALERT_AFTER = 300                 # sunucu bu kadar sn yanıtsızsa uyar (dağıtım ~2 dk sürer)

# Ağ taraması
SCAN_EVERY = int(os.getenv("NOBETCI_SCAN_EVERY", "300"))
SCAN_ENABLED = os.getenv("NOBETCI_SCAN", "1") == "1"

net_events = []        # [{"dur": sn, "_end": monotonic}] — sunucuya henüz iletilmemiş kopmalar
offline_since = None   # internet son kontrolde yoksa kopma başlangıcı (monotonic)
last_latency = None    # son başarılı internet kontrolünün gecikmesi (ms)
last_reconnect = 0.0
server_fail_since = None
server_alert_sent = False
pending_devices = None  # son tarama sonucu; bir sonraki nabızla gider
hostnames = {}          # ip -> ters DNS adı (tekrar sorgulamamak için)
lock = threading.Lock()
dev_lock = threading.Lock()


def log(msg: str) -> None:
    print(msg, flush=True)


def read(path: str) -> str:
    with open(path) as f:
        return f.read().strip()


def sd_notify(msg: str) -> None:
    """systemd'ye durum bildirir (WATCHDOG=1 gibi). systemd dışında sessizce atlanır."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.send(msg.encode())
    except OSError:
        pass


# ---------- İnternet ----------
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


def default_iface():
    try:
        out = subprocess.run(["ip", "-4", "-o", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
        parts = out.split()
        return parts[parts.index("dev") + 1] if "dev" in parts else None
    except Exception:
        return None


def maybe_reconnect_wifi() -> None:
    """İnternet uzun süredir yoksa Wi-Fi bağlantısını yeniden kurmayı dener."""
    global last_reconnect
    if offline_since is None:
        return
    now = time.monotonic()
    if now - offline_since < RECONNECT_AFTER or now - last_reconnect < RECONNECT_COOLDOWN:
        return
    last_reconnect = now
    dev = default_iface() or "wlan0"
    if not dev.startswith("wl"):
        return                           # kablolu bağlantıda dokunma
    log(f"internet {round(now - offline_since)} sn'dir yok; {dev} yeniden bağlanıyor")
    try:
        subprocess.run(["nmcli", "device", "disconnect", dev], capture_output=True, timeout=20)
        time.sleep(2)
        subprocess.run(["nmcli", "device", "connect", dev], capture_output=True, timeout=30)
    except Exception as e:
        log(f"yeniden bağlanma başarısız: {type(e).__name__}")


# ---------- Telegram (yalnızca sunucu izleme uyarıları) ----------
def telegram(text: str) -> None:
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=json.dumps({"chat_id": TG_CHAT, "text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:               # str(e) token'lı URL içerebilir; yalnızca türü yaz
        log(f"telegram gönderilemedi: {type(e).__name__}")


def watch_server(ok: bool) -> None:
    """Pi ve internet sağlamken sunucu uzun süre yanıtsızsa Telegram'dan uyarır."""
    global server_fail_since, server_alert_sent
    now = time.monotonic()
    if ok:
        if server_alert_sent:
            telegram(f"✅ Nöbetçi sunucusu geri geldi ({round((now - server_fail_since) / 60)} dk yanıtsızdı).")
        server_fail_since, server_alert_sent = None, False
        return
    if offline_since is not None:        # sorun internette, sunucuda değil
        server_fail_since = None
        return
    if server_fail_since is None:
        server_fail_since = now
    elif not server_alert_sent and now - server_fail_since >= SERVER_ALERT_AFTER:
        server_alert_sent = True
        telegram(
            "🚨 Nöbetçi sunucusu yanıt vermiyor.\n"
            f"Pi ve internet çalışıyor, sunucuya {round((now - server_fail_since) / 60)} dk'dır ulaşılamıyor.\n"
            "Elektrik/internet bildirimleri sunucu dönene kadar gelmeyecek."
        )


# ---------- Ağ taraması ----------
def local_network():
    """(arayüz, ağ) döndürür; büyük ağlarda taramayı kendi /24'ü ile sınırlar."""
    dev = default_iface()
    if not dev:
        return None
    out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", dev], capture_output=True, text=True, timeout=5).stdout.split()
    if "inet" not in out:
        return None
    iface = ipaddress.ip_interface(out[out.index("inet") + 1])
    net = iface.network if iface.network.prefixlen >= 24 else ipaddress.ip_network(f"{iface.ip}/24", strict=False)
    return dev, net, iface.ip


def resolve(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0].split(".")[0][:40]
    except OSError:
        return ""


def scan_network():
    """UDP paketleriyle ARP çözümlemesini tetikler, /proc/net/arp'ten cihazları okur."""
    found = local_network()
    if not found:
        return None
    dev, net, own = found
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setblocking(False)
        for host in net.hosts():
            if host != own:
                try:
                    s.sendto(b"", (str(host), 9))
                except OSError:
                    pass
    time.sleep(10)                       # eski (stale) kayıtlar yoklanıp elensin
    devices = []
    for line in read("/proc/net/arp").splitlines()[1:]:
        ip, _, flags, mac, _, iface = line.split()[:6]
        if iface == dev and flags == "0x2" and mac != "00:00:00:00:00:00":
            devices.append({"ip": ip, "mac": mac.lower()})
    todo = [d["ip"] for d in devices if d["ip"] not in hostnames]
    if todo:
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            futures = {ip: ex.submit(resolve, ip) for ip in todo}
            for ip, fut in futures.items():
                try:
                    hostnames[ip] = fut.result(timeout=4)
                except Exception:
                    hostnames[ip] = ""
    for d in devices:
        d["host"] = hostnames.get(d["ip"], "")
    return devices


def scan_loop() -> None:
    global pending_devices
    time.sleep(30)                       # açılışta ağın yerleşmesini bekle
    while True:
        try:
            devs = scan_network()
            if devs is not None:
                with dev_lock:
                    pending_devices = devs
        except Exception as e:
            log(f"tarama hatası: {type(e).__name__}")
        time.sleep(SCAN_EVERY)


# ---------- Nabız ----------
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
    global pending_devices
    with lock:
        now = time.monotonic()
        pending = list(net_events)
        with dev_lock:
            devices = pending_devices
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
                "devices": devices,
            },
            timeout=10,
        )
        del net_events[: len(pending)]  # yalnızca başarıyla iletilenleri sil
        with dev_lock:
            if pending_devices is devices:
                pending_devices = None


def listen() -> None:
    """Sunucudan "şimdi ölç" isteğini bekler; gelince anında canlı nabız yollar."""
    while True:
        try:
            if post(WAIT_URL, {}, timeout=40).get("ping"):
                beat(live=True)
        except Exception as e:
            log(f"dinleme hatası: {type(e).__name__}")
            time.sleep(5)


def main() -> None:
    threading.Thread(target=listen, daemon=True).start()
    if SCAN_ENABLED:
        threading.Thread(target=scan_loop, daemon=True).start()
    sd_notify("READY=1")
    last_beat = 0.0
    while True:
        sd_notify("WATCHDOG=1")
        check_internet()
        maybe_reconnect_wifi()
        if time.monotonic() - last_beat >= BEAT_EVERY:
            try:
                beat()
                last_beat = time.monotonic()
                watch_server(True)
            except Exception as e:  # sunucuya ulaşılamıyorsa sessizce devam et
                log(f"nabız gönderilemedi: {type(e).__name__}")
                last_beat = time.monotonic() - BEAT_EVERY + CHECK_EVERY  # 10 sn sonra tekrar dene
                watch_server(False)
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    main()
