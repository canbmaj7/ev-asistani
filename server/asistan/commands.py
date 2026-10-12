"""Telegram komutları: yardım metinleri, `/` menüsü, komut yönlendirme ve güncelleme döngüsü."""
import asyncio

import time

from . import calendars, config, devices, home, invites, members, pharmacy, planned, prayer, quakes, speed, stats, summary
from . import telegram as tg
from .db import kv_set
from .util import log, spawn

_COMMON = (
    "/durum – evin canlı durumu\n/ozet – günlük özet\n/deprem – son 24 saatin depremleri\n"
    "/kesinti – planlı elektrik/su kesintileri\n/eczane – nöbetçi eczaneler\n/ezan – ezan vakitleri\n"
    "/hiz – internet hız testi kayıtları (/hiz test: şimdi ölç)\n"
    "/takvim – takvimini bağla (sabah özetinde görünür)\n/ramazan – sahur/iftar hatırlatması aç/kapat\n"
    "/gecmis – son kesintiler\n/rapor [gün] – kesinti istatistiği\n"
)
_AUTO = (f"\nOtomatik: her sabah {config.SEND_AT}'de özet; Türkiye'de deprem, {config.NEIGHBORHOOD or 'mahallen'} için planlı "
         "kesinti, yaklaşan yağmur/fırtına ve evde elektrik/internet kesintisi olunca anında haber veririm.")
HELP_MEMBER = _COMMON + "/ayril – bildirimlerden çık\n" + _AUTO
HELP_OWNER = _COMMON + (
    "/esik [dk] – alarm eşiği (şimdi/değiştir)\n/bakim [dk] – kesinti mesajlarını sustur\n"
    "/cihazlar – ağdaki cihazlar\n/cihaz SON4 isim – cihaza isim ver\n"
    "/davet – yeni üye için kod üret\n/uyeler – üyeleri listele\n/cikar ID – üyeyi çıkar\n"
) + _AUTO
WELCOME = (
    "✅ Katıldın! Artık evdeki elektrik/internet kesintilerinden, depremlerden haberdar olacaksın; "
    f"her sabah {config.SEND_AT}'de de özet gelecek.\n"
    "📅 Özetinde kendi takvimini görmek istersen: /takvim\n\n" + HELP_MEMBER
)

MEMBER_MENU = [
    {"command": "durum", "description": "Canlı durum: internet, sıcaklık, Wi-Fi"},
    {"command": "ozet", "description": "Günlük özet: hava, kur, takvim, deprem"},
    {"command": "deprem", "description": "Son 24 saatin depremleri"},
    {"command": "kesinti", "description": "Planlı elektrik/su kesintileri"},
    {"command": "eczane", "description": "Nöbetçi eczaneler"},
    {"command": "ezan", "description": "Ezan vakitleri"},
    {"command": "hiz", "description": "İnternet hız testi (/hiz test: şimdi ölç)"},
    {"command": "takvim", "description": "Takvimini bağla (sabah özetinde görünür)"},
    {"command": "ramazan", "description": "Sahur/iftar hatırlatması aç/kapat"},
    {"command": "gecmis", "description": "Son kesintiler"},
    {"command": "rapor", "description": "7 ve 30 günlük kesinti istatistiği"},
    {"command": "yardim", "description": "Komut listesi"},
    {"command": "katil", "description": "Davet koduyla bildirimlere katıl"},
    {"command": "ayril", "description": "Bildirimlerden çık"},
]
OWNER_MENU = [c for c in MEMBER_MENU if c["command"] not in ("katil", "ayril")] + [
    {"command": "esik", "description": "Alarm eşiği: /esik 3 (dakika)"},
    {"command": "bakim", "description": "Kesinti mesajlarını sustur: /bakim 30"},
    {"command": "cihazlar", "description": "Ağdaki cihazlar"},
    {"command": "cihaz", "description": "Cihaza isim ver: /cihaz SON4 isim"},
    {"command": "davet", "description": "Yeni üye için davet kodu üret"},
    {"command": "uyeler", "description": "Üyeleri listele"},
    {"command": "cikar", "description": "Üyeyi çıkar: /cikar ID"},
]


async def _status(chat_id: int) -> None:
    await tg.send(await home.live_status(), to=[chat_id])


async def _speedtest(chat_id: int) -> None:
    await tg.send("⏳ Pi hız testi yapıyor (yaklaşık 1 dakika)…", to=[chat_id])
    r = await home.request_speedtest()
    await tg.send(f"📶 Hız testi: {speed.fmt(r)}\n🔗 Ölçüm: speed.cloudflare.com" if r
                  else "Pi hız testine yanıt vermedi (çevrimdışı ya da bağlantı çok yavaş olabilir).", to=[chat_id])


async def _maintenance(chat_id: int, arg: str) -> None:
    if arg.lower() in ("kapat", "bitir", "0"):
        kv_set("mute_until", 0)
        await tg.send("🔔 Bakım modu kapatıldı; kesinti mesajları yeniden açık.", to=[chat_id])
        return
    minutes = int(arg) if arg.isdigit() else 30
    minutes = max(1, min(minutes, 120))
    kv_set("mute_until", time.time() + minutes * 60)
    await tg.send(f"🔕 Bakım modu: {minutes} dakika boyunca kesinti mesajı gönderilmeyecek. Kapatmak için: /bakim kapat", to=[chat_id])


async def _threshold(chat_id: int, arg: str) -> None:
    if not arg:
        t = home.timeout_sec()
        await tg.send(f"Alarm eşiği şu an {t // 60} dk {t % 60} sn. Değiştirmek için: /esik 3", to=[chat_id])
        return
    try:
        minutes = float(arg.replace(",", "."))
    except ValueError:
        await tg.send("Kullanım: /esik DAKİKA  (örn. /esik 3)", to=[chat_id])
        return
    if not 2 <= minutes <= 60:
        await tg.send("Eşik 2 ile 60 dakika arasında olmalı (Pi 30 sn'de bir nabız yollar).", to=[chat_id])
        return
    kv_set("timeout_sec", int(minutes * 60))
    await tg.send(f"✅ Alarm eşiği {minutes:g} dakika yapıldı.", to=[chat_id])


async def handle(chat_id: int, msg: dict, cmd: str, arg: str) -> None:
    owner = members.is_owner(chat_id)
    arg = arg.strip()
    if cmd == "/katil":
        await invites.join(chat_id, msg, arg, WELCOME)
        return
    if not members.is_member(chat_id):
        await tg.send("Bu bot özeldir. Davet kodun varsa: /katil KOD", to=[chat_id])
        return

    # herkes
    if cmd in ("/durum", "/start"):
        spawn(_status(chat_id))
    elif cmd == "/ozet":
        await tg.send("⏳ Özet hazırlanıyor…", to=[chat_id])
        spawn(summary.send_to(chat_id))
    elif cmd == "/deprem":
        spawn(quakes.reply(chat_id))
    elif cmd == "/takvim":
        await calendars.command(chat_id, msg, arg)
    elif cmd == "/kesinti":
        spawn(planned.reply(chat_id))
    elif cmd == "/eczane":
        spawn(pharmacy.reply(chat_id))
    elif cmd == "/ezan":
        spawn(prayer.reply(chat_id))
    elif cmd == "/ramazan":
        await prayer.toggle_ramadan(chat_id)
    elif cmd == "/hiz":
        if arg.lower() in ("test", "olc", "ölç", "simdi", "şimdi"):
            spawn(_speedtest(chat_id))
        else:
            await tg.send(speed.text(), to=[chat_id])
    elif cmd in ("/gecmis", "/geçmiş"):
        await tg.send(stats.history_text(), to=[chat_id])
    elif cmd == "/rapor":
        days = int(arg) if arg.isdigit() else None
        await tg.send(stats.report_text((max(1, min(days, 365)),)) if days else stats.report_text(), to=[chat_id])
    elif cmd in ("/yardim", "/yardım", "/help"):
        await tg.send(HELP_OWNER if owner else HELP_MEMBER, to=[chat_id])
    elif cmd == "/ayril" and not owner:
        members.remove(chat_id)
        await tg.send("Bildirimlerden çıktın. Tekrar katılmak için yeni bir davet kodu gerekir.", to=[chat_id])
        await tg.send(f"👤 {invites.display_name(msg)} bildirimlerden ayrıldı.", to=[config.OWNER_ID])

    # yalnızca sahip
    elif not owner:
        return
    elif cmd == "/esik":
        await _threshold(chat_id, arg)
    elif cmd == "/bakim":
        await _maintenance(chat_id, arg)
    elif cmd == "/cihazlar":
        await tg.send(devices.text(), to=[chat_id])
    elif cmd == "/cihaz":
        await tg.send(devices.rename(arg), to=[chat_id])
    elif cmd == "/davet":
        await tg.send(
            "Katılacak kişiye şunu ilet:\n1) Telegram'da @evnobetcisi_bot'u aç\n"
            f"2) Şunu yaz: /katil {invites.new_code()}\n\n"
            f"Kod {invites.TTL // 60} dakika geçerli ve tek kullanımlık.", to=[chat_id])
    elif cmd == "/uyeler":
        lines = ["👑 Sen (sahip)"] + [f"👤 {r['name']} — ID {r['chat_id']}" for r in members.listing()]
        await tg.send("\n".join(lines), to=[chat_id])
    elif cmd == "/cikar":
        if not arg.lstrip("-").isdigit():
            await tg.send("Kullanım: /cikar ID (ID'yi /uyeler gösterir)", to=[chat_id])
            return
        await tg.send("Üye çıkarıldı." if members.remove(int(arg)) else "Böyle bir üye yok.", to=[chat_id])


async def loop() -> None:
    """Telegram'dan gelen mesajları çeker (yalnızca özel sohbetler)."""
    await tg.set_commands(MEMBER_MENU, OWNER_MENU)
    offset = 0
    while True:
        try:
            for upd in await tg.get_updates(offset):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                chat = msg.get("chat", {})
                text = (msg.get("text") or "").strip()
                if chat.get("type") != "private":
                    continue
                if text.startswith("/"):
                    head, _, arg = text.partition(" ")
                    await handle(chat["id"], msg, head.split("@")[0].lower(), arg)
                elif calendars.expects_link(chat["id"], text) and members.is_member(chat["id"]):
                    spawn(calendars.save(chat["id"], text, msg.get("message_id")))
        except Exception as e:
            log.error("Telegram polling hatası (%s)", type(e).__name__)
            await asyncio.sleep(5)
