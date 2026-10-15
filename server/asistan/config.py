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
# Dış sitelere kendimizi açıkça tanıtırız: otomatik bir istemciyiz, kaynak kodu şurada
USER_AGENT = "ev-asistani/1.0 (+https://github.com/canbmaj7/ev-asistani)"

# Kesinti izleme
TIMEOUT_SEC = int(os.getenv("TIMEOUT_SEC", "120"))    # başlangıç alarm eşiği (/esik ile değişir)
LIVE_TIMEOUT = int(os.getenv("LIVE_TIMEOUT", "15"))   # /durum canlı yanıt bekleme süresi
WAIT_SEC = 25                                         # Pi'nin uzun bekleme (long-poll) süresi
ONLINE_SEC = 900                                      # cihaz bu süre içinde görüldüyse "çevrimiçi"

# Sabah özeti
SEND_AT = "06:00"                                     # sabit (ortam değişkeniyle değişmez)
SUMMARY_CATCHUP_H = 2                                 # geç açılışta en fazla bu kadar saat telafi
CITY = os.getenv("CITY", "Gebze")
LAT = float(os.getenv("LAT", "40.8028"))
LON = float(os.getenv("LON", "29.4307"))
WATCH_URLS = [u.strip() for u in os.getenv("WATCH_URLS", "").split(",") if u.strip()]

# Deprem
QUAKE_MIN_MAG = float(os.getenv("QUAKE_MIN_MAG", "4.0"))
QUAKE_MAX_OFFSHORE_KM = float(os.getenv("QUAKE_MAX_OFFSHORE_KM", "100"))
QUAKE_POLL_SEC = int(os.getenv("QUAKE_POLL_SEC", "60"))

# Planlı kesintiler (SEDAŞ elektrik, İSU su)
DISTRICT = os.getenv("DISTRICT", "GEBZE")                    # SEDAŞ ilçe adı
NEIGHBORHOOD = "Adem Yavuz"                                  # planlı kesinti uyarıları ve özet yalnızca bu mahalle için
# Kaynak sitelere ne sıklıkla istek atılır (kaynak başına tek istek; hata olursa aralık ikiye katlanır, en çok 1 saat)
SEDAS_POLL_SEC = int(os.getenv("SEDAS_POLL_SEC", "1800"))    # SEDAŞ planlı kesintileri günler önceden duyurur
ISU_POLL_SEC = int(os.getenv("ISU_POLL_SEC", "600"))         # İSU arıza kesintileri aniden çıkabilir
POLL_BACKOFF_MAX_SEC = 3600

# Anlık hava uyarıları
WEATHER_POLL_SEC = int(os.getenv("WEATHER_POLL_SEC", "900"))
QUIET_HOURS = (23, 7)                                        # bu saatlerde yalnızca ciddi uyarılar

# Ezan vakitleri (Diyanet verisi, ezanvakti.emushaf.net ilçe kodu; 9651 = Gebze)
PRAYER_DISTRICT_ID = os.getenv("PRAYER_DISTRICT_ID", "9651")

# Nöbetçi eczane (Habertürk listesi)
PHARMACY_URL = os.getenv("PHARMACY_URL", "https://www.haberturk.com/nobetci-eczaneler/kocaeli/gebze")

# Hız testi (Pi yapar, sunucu kaydeder)
SPEEDTEST_TIMEOUT = 150
UPDATE_TIMEOUT = 90                                          # Pi ajan güncellemesinin yeniden bağlanmasını bekleme süresi
