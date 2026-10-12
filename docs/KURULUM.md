# Kurulum

## 1. Telegram botu
1. Telegram'da **@BotFather** → `/newbot`
2. Ad: `Ev Nöbetçisi`, kullanıcı adı: `evnobetcisi_bot` (alınmışsa başka bir ad; "bot" ile bitmeli)
3. Verilen **token**'ı sakla.
4. Bota `/start` yaz, sonra tarayıcıda `https://api.telegram.org/bot<TOKEN>/getUpdates` aç; `chat.id` değeri **chat id**'dir.

## 2. Sunucu
```
BEAT_TOKEN=$(openssl rand -hex 24)
# .env dosyası: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, BEAT_TOKEN
docker compose up -d --build
```
HTTPS için bir alan adı gerekir (kendi alan adın ya da ücretsiz `nobetci.<SUNUCU-IP-tireli>.sslip.io`, örn. IP `1.2.3.4` ise `nobetci.1-2-3-4.sslip.io`). `.env` dosyasına `NOBETCI_DOMAIN=<alan-adı>` ekle.

Coolify kullanıyorsan: *New Resource → Public Repository*, build pack **Dockerfile**, base directory `/server`, port `8000`, alan adını Domains'e yaz; ortam değişkenlerini Coolify'a gir ve `/data` için kalıcı depolama ekle.

Sabah özeti için `SEND_AT` (08:00), `CITY`/`LAT`/`LON`, `WATCH_URLS` (virgülle ayrılmış site adresleri) tanımlanır. Deprem eşiği `QUAKE_MIN_MAG` (4.0). Takvim sunucuda ayarlanmaz: her kişi botta `/takvim` ile kendi linkini bağlar.

Web paneli için `PANEL_PASSWORD` ortam değişkenini tanımla (boşsa panel kapalı kalır). Alarm eşiği başlangıç değeri `TIMEOUT_SEC` (varsayılan 120 sn), sonradan `/esik` ile değişir.

## 3. Pi (Raspberry Pi 3)
1. Raspberry Pi Imager ile **Raspberry Pi OS Lite (64-bit)** yaz; ayarlarda ana bilgisayar adı, kullanıcı, SSH ve Wi-Fi/Ethernet tanımla.
2. Pi'yi prize tak, SSH ile bağlan, `pi/` klasörünü kopyala.
3. (İsteğe bağlı) Sunucu çökerse Telegram'dan haber vermesi için `pi/extra.env` dosyasına `NOBETCI_TG_TOKEN=...` ve `NOBETCI_TG_CHAT=...` yaz.
4. `sudo bash setup.sh https://<alan-adı>/beat <BEAT_TOKEN>`
5. Her şey çalıştıktan sonra SD kartı korumak için bkz. [`PI-BAKIM.md`](PI-BAKIM.md).

## 4. Test (`/durum`, `/rapor` ve panel de denenebilir)
- `/durum` → 🟢
- Ethernet/Wi-Fi'yi 3 dk kes → ⚠️ alarm, geri takınca 🌐 internet kesintisi bitti
- Fişi çek, 3 dk bekle, tak → ⚡ elektrik kesintisi bitti
