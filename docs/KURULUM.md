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

## 3. Pi (Raspberry Pi 3)
1. Raspberry Pi Imager ile **Raspberry Pi OS Lite (64-bit)** yaz; ayarlarda ana bilgisayar adı, kullanıcı, SSH ve Wi-Fi/Ethernet tanımla.
2. Pi'yi prize tak, SSH ile bağlan, `pi/` klasörünü kopyala.
3. `sudo bash setup-pi.sh https://<alan-adı>/beat <BEAT_TOKEN>`

## 4. Test
- `/durum` → 🟢
- Ethernet/Wi-Fi'yi 3 dk kes → ⚠️ alarm, geri takınca 🌐 internet kesintisi bitti
- Fişi çek, 3 dk bekle, tak → ⚡ elektrik kesintisi bitti
