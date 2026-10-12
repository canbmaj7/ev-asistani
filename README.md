# Ev Nöbetçisi

Evdeki bir Raspberry Pi ile **elektrik** ve **internet** kesintilerini izleyip **Telegram'dan** haber veren küçük bir sistem.

```
Raspberry Pi ──(30 sn'de bir nabız)──▶ sunucu (Docker) ──▶ Telegram
```

- **Pi ajanı** (`pi/`): sunucuya düzenli nabız yollar, interneti 3 bağımsız hedefe bağlanarak kendisi de ölçer. Yalnızca Python standart kütüphanesi.
- **Sunucu** (`server/`): FastAPI + SQLite. Nabız kesilince uyarır; geri gelince kesintinin **elektrik mi internet mi** olduğunu (Pi'nin `boot_id` değişimine bakarak) ve süresini bildirir.
- **Telegram komutları:** `/durum`, `/gecmis`.

Kurulum için [`docs/KURULUM.md`](docs/KURULUM.md).

## Güvenlik
- `/beat` gizli bir token ister; Telegram komutları yalnızca tanımlı sohbetten kabul edilir.
- Token'lar ve sohbet kimliği `.env` içinde tutulur, repoya girmez.

## Güncelleme
Bu repoya `main` dalına push yapıldığında Coolify otomatik olarak yeniden derleyip yayına alır (GitHub webhook).
