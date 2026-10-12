"""Ortam değişkenlerinden okunan ayarlar (tek yer)."""
import os
from zoneinfo import ZoneInfo

# Zorunlu
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OWNER_ID = int(os.environ["TELEGRAM_CHAT_ID"])        # botun sahibi
BEAT_TOKEN = os.environ["BEAT_TOKEN"]                 # Pi ↔ sunucu paylaşılan sır

# Genel
DB_PATH = os.getenv("DB_PATH", "/data/nobetci.db")
TZ = ZoneInfo(os.getenv("TZ", "Europe/Istanbul"))
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")      # boşsa web paneli kapalı
USER_AGENT = "ev-asistani/1.0"

# Kesinti izleme
TIMEOUT_SEC = int(os.getenv("TIMEOUT_SEC", "120"))    # başlangıç alarm eşiği (/esik ile değişir)
LIVE_TIMEOUT = int(os.getenv("LIVE_TIMEOUT", "15"))   # /durum canlı yanıt bekleme süresi
WAIT_SEC = 25                                         # Pi'nin uzun bekleme (long-poll) süresi
ONLINE_SEC = 900                                      # cihaz bu süre içinde görüldüyse "çevrimiçi"

# Sabah özeti
SEND_AT = os.getenv("SEND_AT", "08:00")
SUMMARY_CATCHUP_H = 2                                 # geç açılışta en fazla bu kadar saat telafi
CITY = os.getenv("CITY", "Gebze")
LAT = float(os.getenv("LAT", "40.8028"))
LON = float(os.getenv("LON", "29.4307"))
WATCH_URLS = [u.strip() for u in os.getenv("WATCH_URLS", "").split(",") if u.strip()]

# Deprem
QUAKE_MIN_MAG = float(os.getenv("QUAKE_MIN_MAG", "4.0"))
QUAKE_MAX_OFFSHORE_KM = float(os.getenv("QUAKE_MAX_OFFSHORE_KM", "100"))
QUAKE_POLL_SEC = int(os.getenv("QUAKE_POLL_SEC", "60"))
