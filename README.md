# Ev Nöbetçisi — Ev Asistanı

Evdeki bir Raspberry Pi ve küçük bir sunucuyla çalışan, Telegram üzerinden konuşan **ev asistanı botu** (Telegram'da adı *Ev Asistanı*):

- ⚡ **Elektrik ve internet kesintisi** bildirimi (hangisi olduğunu ve süresini ayırt eder)
- ☀️ **Sabah özeti**: her gün herkese hava, döviz kuru, **kişinin kendi takvimi**, deprem ve ev durumu tek mesajda (sahibe ayrıca sunucu durumu)
- 🌍 **Deprem uyarısı**: Türkiye'de deprem olunca anında
- 📡 **Ağ bekçisi**: ağa yeni bir cihaz bağlanınca haber
- 📊 **Web paneli**: canlı durum, istatistik, grafik

```
Raspberry Pi ──(nabız, ağ taraması)──▶ sunucu (Docker) ──▶ Telegram
                                          ▲
              Open-Meteo · TCMB · AFAD · Google Takvim
```

## Telegram komutları
| Komut | Ne yapar | Kim |
|---|---|---|
| `/durum` | Pi'ye **anında** ölçüm yaptırır: internet gecikmesi, CPU sıcaklığı, Wi-Fi sinyali, boş RAM, açık kalma süresi | herkes |
| `/ozet` | Sabah özetini şimdi gönderir (sunucu bölümü yalnızca sahipte) | herkes |
| `/deprem` | Son 24 saatte Türkiye'de M≥4 depremler | herkes |
| `/takvim` | Kendi takvimini bağla (`/takvim sil` ile kaldır) | herkes |
| `/gecmis` | Son 10 kesinti | herkes |
| `/rapor [gün]` | Kesinti sayısı, toplam süre, en uzun kesinti, erişilebilirlik (varsayılan 7 ve 30 gün) | herkes |
| `/esik [dk]` | Alarm eşiğini göster/değiştir (2-60 dk) | sahip |
| `/cihazlar`, `/cihaz SON4 isim` | Ağdaki cihazlar, isim verme | sahip |
| `/davet`, `/uyeler`, `/cikar ID` | Üye yönetimi (tek kullanımlık, 10 dk geçerli davet kodu) | sahip |
| `/katil KOD`, `/ayril` | Bildirimlere katıl/çık | üye |

`/` yazınca Telegram komut menüsü açılır; sahip ve üyeler kendi listelerini görür.

## Nasıl çalışır?

### Kesinti izleme
- Pi, sunucuya 30 sn'de bir nabız yollar. Nabız kesilirse (varsayılan 2 dk) sunucu uyarır.
- Geri gelince Pi'nin `boot_id`'sine bakılır: değiştiyse **elektrik**, değişmediyse **internet** kesintisidir. Elektrik kesintisinin süresine Pi'nin açılış süresi katılmaz.
- Pi interneti kendisi de ölçer (3 bağımsız hedef); kısa kopmaları bağlantı gelince bildirir.
- `/durum` için Pi, sunucuya uzun bekleme (`POST /wait`) bağlantısı açık tutar; modemde port açmak gerekmez.

### Sabah özeti
Her gün `SEND_AT` (varsayılan 08:00) saatinde **herkese kendi sürümü** gider: sahip ve üyeler aynı hava/kur/deprem/ev bilgisini alır, takvim kişiye özeldir, sunucu bölümü yalnızca sahibe gider.

| Bölüm | Kaynak |
|---|---|
| Hava (`CITY`, `LAT`/`LON`) | [Open-Meteo](https://open-meteo.com), anahtarsız |
| Kur (USD, EUR, GBP ve günlük değişim) | TCMB `today.xml` |
| Takvim (kişiye özel, isteğe bağlı) | Kişinin `/takvim` ile bağladığı .ics linki (Google Takvim "iCal biçiminde gizli adres", iCloud, Outlook) |
| Deprem (son 24 sa) | AFAD |
| Sunucu (yalnızca sahip) | RAM/disk/yük + `WATCH_URLS` sitelerinin durumu, yanıt süresi, SSL bitişi |
| Ev | Pi'nin durumu ve son 24 saatin kesintileri |

Sunucu o saatten en geç 2 saat sonra açılırsa o günün özetini telafi eder.

**Takvim gizliliği:** Bot linki kişinin kendisinden ister ve yalnızca o kişinin özetinde kullanır. Linkin geçtiği mesaj kaydedildikten sonra sohbetten silinir. Yalnızca internetteki `https` adresleri kabul edilir (sunucunun iç ağına istek attırılamaz).

### Deprem uyarısı
- AFAD 60 sn'de bir sorgulanır; Türkiye'de **M≥4.0** (`QUAKE_MIN_MAG`) yeni deprem olunca **tüm üyelere** anında mesaj gider: büyüklük, yer, saat, derinlik, konuma uzaklık, harita linki.
- Kıyıdan 100 km'den uzak deniz depremleri elenir (`QUAKE_MAX_OFFSHORE_KM`). AFAD tarihleri UTC'dir, yerel saate çevrilir.

### Ağ bekçisi
Pi kendi ağını 5 dakikada bir tarar (ARP, ek yazılım gerekmez). İlk tarama başlangıç listesi sayılır; sonradan görülen cihaz için sahibe bildirim gider. Not: telefonların "özel Wi-Fi adresi" özelliği aynı cihazı farklı MAC ile gösterebilir.

### Web paneli
`https://<alan-adı>/panel` (HTTP Basic, şifre `PANEL_PASSWORD`): canlı durum, 7/30 günlük istatistik, günlük kesinti grafiği, kesinti geçmişi, ağ cihazları. Şifre tanımlı değilse panel kapalıdır.

### Dayanıklılık
- systemd watchdog (ajan 120 sn donarsa yeniden başlar), donanım watchdog'u (sistem donarsa Pi yeniden açılır)
- İnternet 3 dk yoksa Wi-Fi yeniden bağlanma
- Sunucu 5 dk'dan uzun yanıtsızsa Pi Telegram'a doğrudan yazar (`NOBETCI_TG_TOKEN`/`NOBETCI_TG_CHAT`)
- SD kartı korumak için salt okunur kök dosya sistemi ([`docs/PI-BAKIM.md`](docs/PI-BAKIM.md))

## Kurulum
[`docs/KURULUM.md`](docs/KURULUM.md). `main` dalına push yapıldığında Coolify otomatik olarak yeniden derleyip yayına alır (GitHub webhook).

## Güvenlik
- `/beat` ve `/wait` gizli bir token ister; Telegram komutları yalnızca sahip ve davetle katılan üyelerden kabul edilir.
- Token'lar, sohbet kimliği ve panel şifresi `.env` / sunucu ortam değişkenlerinde, kişisel takvim linkleri botun veritabanında tutulur; hiçbiri repoya girmez.
- Bot token'ı günlüklere yazılmaz (istisnalarda yalnızca hata türü loglanır).
