# Ev Nöbetçisi

Evdeki bir Raspberry Pi ile **elektrik** ve **internet** kesintilerini izleyip **Telegram'dan** haber veren küçük bir sistem.

```
Raspberry Pi ──(30 sn'de bir nabız)──▶ sunucu (Docker) ──▶ Telegram
```

- **Pi ajanı** (`pi/`): sunucuya düzenli nabız yollar, interneti 3 bağımsız hedefe bağlanarak kendisi de ölçer. Yalnızca Python standart kütüphanesi.
- **Sunucu** (`server/`): FastAPI + SQLite. Nabız kesilince uyarır; geri gelince kesintinin **elektrik mi internet mi** olduğunu (Pi'nin `boot_id` değişimine bakarak) ve süresini bildirir.
- **Telegram komutları:**
  - `/durum`: Pi'ye **anında** ölçüm yaptırıp canlı sonucu yazar (internet gecikmesi, CPU sıcaklığı, Wi-Fi sinyali, boş RAM, açık kalma süresi). Pi yanıt vermezse bunu da söyler.
  - `/gecmis`: son 10 kesinti.
  - `/rapor [gün]`: kesinti sayısı, toplam süre, en uzun kesinti ve erişilebilirlik yüzdesi (varsayılan 7 ve 30 gün).
  - `/esik [dk]` (sahip): alarm eşiğini göster/değiştir (2-60 dk).
  - `/cihazlar`, `/cihaz SON4 isim` (sahip): ağdaki cihazlar ve isim verme.
  - `/davet` (sahip): tek kullanımlık, 10 dakika geçerli kod üretir. Başkası bota `/katil KOD` yazınca bildirimlere dahil olur.
  - `/uyeler`, `/cikar ID` (sahip), `/ayril` (üye), `/yardim`.
- **Web paneli:** `https://<alan-adı>/panel` (HTTP Basic, şifre `PANEL_PASSWORD`). Canlı durum, 7/30 günlük istatistik, günlük kesinti grafiği, kesinti geçmişi ve ağ cihazları. Şifre tanımlı değilse panel kapalıdır.
- **Ağ cihazı takibi:** Pi, kendi ağını 5 dakikada bir tarar (ARP, ek yazılım gerekmez). İlk tarama başlangıç listesi sayılır; sonradan görülen cihaz için sahibe bildirim gider. Not: telefonların "özel/rastgele Wi-Fi adresi" özelliği aynı cihazı farklı MAC ile gösterebilir.
- **Sunucu izleme:** Pi, sunucuya 5 dakikadan uzun ulaşamıyor ama interneti sağlamsa Telegram'a doğrudan yazar (isteğe bağlı, `NOBETCI_TG_TOKEN`/`NOBETCI_TG_CHAT`).
- **Dayanıklılık:** systemd watchdog (ajan 120 sn donarsa yeniden başlar), donanım watchdog'u (sistem donarsa Pi yeniden açılır), internet 3 dk yoksa Wi-Fi yeniden bağlanma, SD kartı korumak için salt okunur kök dosya sistemi ([`docs/PI-BAKIM.md`](docs/PI-BAKIM.md)).
- **Çoklu kullanıcı:** Sahip `TELEGRAM_CHAT_ID` ile belirlenir; üyeler SQLite'ta tutulur. Alarm herkese gider, `/durum` yanıtı yalnızca soran kişiye.
- **Canlı kanal:** Pi, sunucuya uzun bekleme (`POST /wait`) bağlantısı açık tutar; `/durum` gelince sunucu bu kanaldan Pi'yi uyandırır. Pi dışarıya bağlandığı için modemde port açmak gerekmez.

Kurulum için [`docs/KURULUM.md`](docs/KURULUM.md).

## Güvenlik
- `/beat` gizli bir token ister; Telegram komutları yalnızca tanımlı sohbetten kabul edilir.
- Token'lar ve sohbet kimliği `.env` içinde tutulur, repoya girmez.

## Güncelleme
Bu repoya `main` dalına push yapıldığında Coolify otomatik olarak yeniden derleyip yayına alır (GitHub webhook).
