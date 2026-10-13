# Ev Nöbetçisi — Ev Asistanı

Evdeki bir Raspberry Pi ve küçük bir sunucuyla çalışan, Telegram üzerinden konuşan **ev asistanı botu** (Telegram'da adı *Ev Asistanı*):

- ⚡ **Elektrik ve internet kesintisi** bildirimi (hangisi olduğunu ve süresini ayırt eder)
- ☀️ **Sabah özeti**: her gün herkese hava, döviz kuru, **kişinin kendi takvimi**, deprem ve ev durumu tek mesajda (sahibe ayrıca sunucu durumu)
- 🌍 **Deprem uyarısı**: Türkiye'de deprem olunca anında
- 🔌 **Planlı kesinti uyarısı**: SEDAŞ (elektrik) ve İSU (su) mahalleni etkileyen bir kesinti duyurunca önceden haber
- 🌧 **Anlık hava uyarıları**: yağmur başlamak üzere, kuvvetli rüzgâr, fırtına, sağanak, gece don
- 🕌 **Ezan vakitleri** (Diyanet) ve isteğe bağlı Ramazan sahur/iftar hatırlatması
- 💊 **Nöbetçi eczane** listesi
- 📶 **Gece hız testi**: Pi her gece interneti ölçer, kayıtlar tutulur
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
| `/kesinti` | İlçedeki planlı elektrik/su kesintileri (7 gün) | herkes |
| `/eczane` | Nöbetçi eczaneler (adres, telefon, harita) | herkes |
| `/ezan` | Bugünün ezan vakitleri ve bir sonraki vakte kalan süre | herkes |
| `/ramazan` | Sahur/iftar hatırlatmasını aç/kapat (yalnızca Ramazan'da çalışır) | herkes |
| `/hiz`, `/hiz test` | Hız testi kayıtları; şimdi ölç | herkes |
| `/bakim [dk]` | Kesinti mesajlarını geçici sustur (Pi bakımı için) | sahip |
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
| Hava kalitesi | Open-Meteo Air Quality (Avrupa AQI, PM2.5, PM10) |
| Ezan vakitleri | Diyanet verisi (`ezanvakti.emushaf.net`, ilçe bazında) |
| Planlı kesintiler (bugün/yarın) | SEDAŞ planlı kesinti listesi, İSU su kesintileri |
| Deprem (son 24 sa) | AFAD |
| Hız testi | Pi'nin son gece ölçümü |
| Sunucu (yalnızca sahip) | RAM/disk/yük + `WATCH_URLS` sitelerinin durumu, yanıt süresi, SSL bitişi |
| Ev | Pi'nin durumu ve son 24 saatin kesintileri |

Sunucu o saatten en geç 2 saat sonra açılırsa o günün özetini telafi eder.

**Takvim gizliliği:** Bot linki kişinin kendisinden ister ve yalnızca o kişinin özetinde kullanır. Linkin geçtiği mesaj kaydedildikten sonra sohbetten silinir. Yalnızca internetteki `https` adresleri kabul edilir (sunucunun iç ağına istek attırılamaz).

### Deprem uyarısı
- AFAD 60 sn'de bir sorgulanır; Türkiye'de **M≥4.0** (`QUAKE_MIN_MAG`) yeni deprem olunca **tüm üyelere** anında mesaj gider: büyüklük, yer, saat, derinlik, konuma uzaklık, harita linki.
- Kıyıdan 100 km'den uzak deniz depremleri elenir (`QUAKE_MAX_OFFSHORE_KM`). AFAD tarihleri UTC'dir, yerel saate çevrilir.

### Planlı kesintiler
SEDAŞ'ın planlı kesinti listesi (`DISTRICT` ilçesi) ve İSU ana sayfasındaki "Su kesintileri" kaydırıcısı 3 saatte bir okunur. SEDAŞ yapılandırılmış veri verir (mahalle, sokak, saat); İSU ise serbest metin duyurur ve tüm Kocaeli'yi kapsar, bu yüzden yalnızca ilçeyi ya da mahalleyi anan duyurular alınır, mahalle metinde aranır. İSU sayfa yapısı değişirse sessizce "kesinti yok" denmez, hata günlüğe yazılır. `NEIGHBORHOOD` mahallesini etkileyen **yeni** bir kesinti görülünce herkese mesaj gider (tarih, saat, sokaklar, kaynak bağlantısı). Mahalle adları Türkçe karakter ve yazım farklarına duyarsız eşleştirilir. `NEIGHBORHOOD` boşsa anlık uyarı gönderilmez, özet yine ilçeyi listeler.

### Anlık hava uyarıları
Open-Meteo 15 dakikada bir sorgulanır: 1 saat içinde başlayacak yağmur, ≥60 km/sa rüzgâr hamlesi, gök gürültülü fırtına, 6 saatte ≥20 mm yağış, akşam saatlerinde gece don tahmini. Her uyarının tekrar süresi vardır; 23:00–07:00 arası yalnızca ciddi olanlar (≥80 km/sa rüzgâr, sağanak) gönderilir.

### Hız testi
Pi her gece `NOBETCI_SPEEDTEST_AT` (03:30) saatinde Cloudflare'in hız testi uç noktalarıyla indirme/yükleme/ping ölçer (ek yazılım gerekmez, ~30 MB veri); `/hiz test` ile istenince de ölçer. Sonuçlar sunucuda saklanır: `/hiz`, sabah özeti ve panelde grafik.

### Ağ bekçisi
Pi kendi ağını 5 dakikada bir tarar (ARP, ek yazılım gerekmez). İlk tarama başlangıç listesi sayılır; sonradan görülen cihaz için sahibe bildirim gider. Not: telefonların "özel Wi-Fi adresi" özelliği aynı cihazı farklı MAC ile gösterebilir.

### Web paneli
`https://<alan-adı>/panel` (HTTP Basic, şifre `PANEL_PASSWORD`): canlı durum, 7/30 günlük istatistik, günlük kesinti grafiği, kesinti geçmişi, ağ cihazları. Şifre tanımlı değilse panel kapalıdır.

### Dayanıklılık
- systemd watchdog (ajan 120 sn donarsa yeniden başlar), donanım watchdog'u (sistem donarsa Pi yeniden açılır)
- İnternet 3 dk yoksa Wi-Fi yeniden bağlanma
- Sunucu 5 dk'dan uzun yanıtsızsa Pi Telegram'a doğrudan yazar (`NOBETCI_TG_TOKEN`/`NOBETCI_TG_CHAT`)
- SD kartı korumak için salt okunur kök dosya sistemi ([`docs/PI-BAKIM.md`](docs/PI-BAKIM.md))

## Proje yapısı
```
pi/                       Raspberry Pi ajanı (yalnızca Python standart kütüphanesi)
  heartbeat.py            nabız, internet ölçümü, ağ taraması, watchdog, sunucu izleme
  setup.sh                kurulum (systemd servisi + watchdog)
  systemd/                nobetci.service, watchdog.conf
server/                   sunucu (Docker / Coolify)
  asistan/
    main.py               FastAPI uygulaması, arka plan döngüleri, /beat ve /wait
    config.py             tüm ayarlar (ortam değişkenleri)
    db.py                 SQLite şeması ve anahtar-değer deposu
    telegram.py           Bot API: gönder, sil, menü, güncellemeler
    commands.py           Telegram komutları ve yetkiler
    home.py               Pi izleme: kesinti tespiti, canlı ölçüm
    stats.py              kesinti geçmişi ve istatistik
    devices.py            ağ bekçisi
    summary.py            sabah özeti (kişiye özel)
    quakes.py             anlık deprem uyarısı
    planned.py            planlı elektrik/su kesintileri
    weather_alerts.py     anlık hava uyarıları
    prayer.py             ezan vakitleri, Ramazan hatırlatması
    pharmacy.py           nöbetçi eczane
    speed.py              hız testi kayıtları
    calendars.py          kişisel takvim bağlama
    members.py, invites.py  üyeler ve davet kodları
    panel.py, static/     web paneli
    sources/              dış veri: weather, rates, quakes, calendar, server, outages, prayer, pharmacy
  tests/                  pytest (internet gerektirenler: `pytest -m network`)
docs/                     kurulum ve Pi bakımı
```

## Geliştirme
```
cd server
pip install -r requirements-dev.txt
pytest                # 53 test, internet gerekmez
pytest -m network     # gerçek servislere bağlanan 10 test
```
Her push'ta GitHub Actions testleri çalıştırır.

## Kurulum
[`docs/KURULUM.md`](docs/KURULUM.md). `main` dalına push yapıldığında Coolify otomatik olarak yeniden derleyip yayına alır (GitHub webhook).

## Güvenlik
- `/beat` ve `/wait` gizli bir token ister; Telegram komutları yalnızca sahip ve davetle katılan üyelerden kabul edilir.
- Token'lar, sohbet kimliği ve panel şifresi `.env` / sunucu ortam değişkenlerinde, kişisel takvim linkleri botun veritabanında tutulur; hiçbiri repoya girmez.
- Bot token'ı günlüklere yazılmaz (istisnalarda yalnızca hata türü loglanır).
- Mahalle gibi konum bilgileri yalnızca sunucu ortam değişkenlerinde tutulur.
- Bildirimlerde bilginin alındığı kaynak bağlantısı yer alır.
