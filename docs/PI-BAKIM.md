# Pi bakımı ve SD kart koruması

Elektrik kesintisini izleyen bir Pi, tam da elektrik gidip gelirken çalıştığı için SD kartı
bozulmaya açıktır. Bunu azaltmak için kök dosya sistemi **salt okunur** yapılır (overlay):
tüm yazmalar RAM'e gider, SD kartın içeriği değişmez. Ajan hiçbir şeyi diske kaydetmez
(durumun tamamı sunucudadır), bu yüzden bu güvenlidir.

## Açmak
```
sudo raspi-config nonint enable_overlayfs   # kök dosya sistemi
sudo raspi-config nonint enable_bootro      # /boot salt okunur
sudo reboot
```
Kontrol: `raspi-config nonint get_overlay_now` → `0` ise overlay etkin.

## Ajanı güncellemek
`/pi_guncelle` (README'deki imzalı güncelleme): overlay'e dokunmadan, birkaç saniyede. Aşağıdaki yöntem yalnızca başlatıcı, systemd dosyaları, `release.pub` ve ağ ayarı gibi nadir değişenler için.

## Bir şeyi değiştirmek (başlatıcı, systemd, ağ vb.)
Overlay açıkken yaptığın değişiklikler yeniden başlatmada silinir. Kalıcı değişiklik için:
```
sudo raspi-config nonint disable_overlayfs
sudo raspi-config nonint disable_bootro
sudo reboot
# ... değişikliği yap (ör. heartbeat.py'yi kopyala, servisi yeniden başlat) ...
sudo raspi-config nonint enable_overlayfs
sudo raspi-config nonint enable_bootro
sudo reboot
```
> Her yeniden başlatma sunucuya "kısa elektrik kesintisi" olarak yansır (Pi'nin `boot_id`'si değişir).
> Bu bakım sırasında gelen bu mesajlar beklenendir.

## Watchdog
- **Ajan:** systemd, ajan 120 sn "yaşıyorum" demezse servisi yeniden başlatır.
- **Donanım:** `RuntimeWatchdogSec=30` (`/etc/systemd/system.conf.d/nobetci-watchdog.conf`); sistem donarsa Pi kendini yeniden açar.
- Watchdog kaynaklı bir yeniden başlatma da sunucuda "elektrik kesintisi" gibi görünür; ayırt edilemez.

## Güç
Pi 3 için en az **5V / 2.5A** adaptör ve kısa, kalın bir kablo kullan. Kontrol:
`vcgencmd get_throttled` → `0x0` ideal. `0x50000` açılıştan beri düşük voltaj görüldüğünü söyler.
