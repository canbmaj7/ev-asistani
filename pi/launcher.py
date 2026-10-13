#!/usr/bin/env python3
"""Nöbetçi başlatıcı: ajanın en son İMZALI sürümünü alıp çalıştırır.

Pi'nin kök dosya sistemi salt okunur (overlay) olduğu için ajan diske değil RAM'e (/run) indirilir;
böylece güncelleme için yeniden başlatma ya da overlay'i kapatma gerekmez.

Güvenlik: ajan root olarak çalışır. İndirilen dosya yalnızca, ayrı bir gizli anahtarla atılmış geçerli
bir Ed25519 imzası varsa çalıştırılır (doğrulama: openssl). Repoya ya da sunucuya erişen biri bile
imzasız/yanlış imzalı dosyayı Pi'ye çalıştıramaz. Sürüm numarası geriye gidemez (eski imzalı dosya
yeniden oynatılamaz). İnternet/GitHub/imza sorunu varsa diskteki yedek sürümle devam edilir.

Bu dosya bilerek küçük ve sade tutulur; değişmesi gerekirse Pi'de overlay kapatılarak güncellenir.
Yalnızca standart kütüphane ve `openssl` komutu.
"""
import base64
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

BASE = os.environ.get("NOBETCI_RELEASE_URL", "https://raw.githubusercontent.com/canbmaj7/ev-asistani/main/pi")
PUBKEY = os.environ.get("NOBETCI_RELEASE_PUB", "/opt/nobetci/release.pub")
BAKED = os.environ.get("NOBETCI_BAKED_AGENT", "/opt/nobetci/heartbeat.py")
RUN_DIR = os.environ.get("RUNTIME_DIRECTORY", "/run/nobetci")
MAX_BYTES = 300_000
VERSION_RE = re.compile(rb"^AGENT_VERSION\s*=\s*(\d+)[ \t]*(?:#.*)?$", re.M)


def log(msg: str) -> None:
    print(f"başlatıcı: {msg}", flush=True)


def version_of(data: bytes):
    m = VERSION_RE.search(data)
    return int(m.group(1)) if m else None


def fetch(url: str, timeout: float = 20) -> bytes:
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}t={int(time.time())}",          # CDN önbelleğini atla
                                 headers={"User-Agent": "ev-asistani-launcher", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("dosya çok büyük")
    return data


def verify(data: bytes, sig_b64: bytes, pubkey: str = PUBKEY, workdir: str | None = None) -> bool:
    """Ed25519 imzasını openssl ile doğrular. Geçersiz her durumda False."""
    try:
        sig = base64.b64decode(sig_b64.strip(), validate=True)
    except ValueError:
        return False
    with tempfile.TemporaryDirectory(dir=workdir) as d:         # dir root'a özel: doğrulanan dosya değiştirilemez
        os.chmod(d, 0o700)
        f, s = os.path.join(d, "agent"), os.path.join(d, "sig")
        with open(f, "wb") as fh:
            fh.write(data)
        with open(s, "wb") as fh:
            fh.write(sig)
        try:
            r = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", pubkey, "-rawin",
                                "-in", f, "-sigfile", s], capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return False
    return r.returncode == 0


def read_file(path: str):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def choose(base: str = BASE, pubkey: str = PUBKEY, baked: str = BAKED, run_dir: str = RUN_DIR) -> str:
    """Çalıştırılacak ajan dosyasının yolunu döndürür (indirilen doğrulanmış sürüm ya da yedek)."""
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    cached_path = os.path.join(run_dir, "heartbeat.py")
    baked_v = version_of(read_file(baked) or b"") or 0
    cached = read_file(cached_path)
    floor = max(baked_v, version_of(cached or b"") or 0)       # bu sürümden eskisi kabul edilmez
    fallback = cached_path if cached is not None else baked
    try:
        for attempt in (1, 2):                                   # CDN'de py/sig geçici uyumsuzsa bir kez daha dene
            data, sig = fetch(base + "/heartbeat.py"), fetch(base + "/heartbeat.py.sig")
            v = version_of(data)
            if v is None:
                raise ValueError("indirilen dosyada AGENT_VERSION yok")
            if v < floor:
                raise ValueError(f"eski sürüm reddedildi ({v} < {floor})")
            if verify(data, sig, pubkey, run_dir):
                break
            if attempt == 2:
                raise ValueError("imza geçersiz")
            time.sleep(5)
        if data != cached:
            tmp = cached_path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, cached_path)
            log(f"ajan güncellendi: sürüm {v}")
        else:
            log(f"ajan güncel: sürüm {v}")
        return cached_path
    except Exception as e:
        log(f"güncelleme alınamadı ({type(e).__name__}: {e}); {'önceki doğrulanmış' if cached is not None else 'yedek'} sürümle devam")
        return fallback


def main() -> None:
    path = choose()
    log(f"çalıştırılıyor: {path}")
    os.execv(sys.executable, [sys.executable, path])           # PID değişmez: systemd watchdog bildirimi sürer


if __name__ == "__main__":
    main()
