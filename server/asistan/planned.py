"""Planlı elektrik (SEDAŞ) ve su (İSU) kesintileri.

Mahalleyi etkileyen yeni bir planlı kesinti görülünce herkese anında haber verir;
sabah özetinde ilçenin bugün/yarın listesi görünür.
"""
import asyncio
import json
import random
import time
from datetime import date, datetime, timedelta

from . import config
from . import telegram as tg
from .db import kv_json, kv_set
from .sources import outages as src
from .sources.common import client
from .util import log, norm

GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def affects_me(o: dict) -> bool:
    if not config.NEIGHBORHOOD:
        return False
    text = o.get("areas", "") + " " + o.get("title", "") + " " + o.get("detail", "")
    return norm(config.NEIGHBORHOOD) in norm(text)


def tidy_names(text: str) -> str:
    """SEDAŞ her adı hem Türkçe karakterli hem karaktersiz yazar (CUMHURIYET, CUMHURİYET): tekilleştir,
    Türkçe karakterli olanı tercih et."""
    best: dict[str, str] = {}
    for name in (n.strip() for n in text.split(",")):
        key = norm(name)
        if not key:
            continue
        accents = lambda v: sum(ch in "İıŞşÇçÖöÜüĞğ" for ch in v)  # noqa: E731
        if key not in best or accents(name) > accents(best[key]):
            best[key] = name
    return ", ".join(best.values())


def short_streets(text: str, limit: int = 160) -> str:
    """Uzun sokak listesini kelime ortasında kesmeden kısaltır: '… +14 sokak daha'."""
    items, seen = [], set()
    for st in (x.strip() for x in text.split(",")):
        if st and norm(st) not in seen:
            seen.add(norm(st))
            items.append(st)
    out, used = [], 0
    for i, st in enumerate(items):
        if used + len(st) + 2 > limit and out:
            return ", ".join(out) + f" … +{len(items) - i} sokak daha"
        out.append(st)
        used += len(st) + 2
    return ", ".join(out)


def day_label(d: date, today: date) -> str:
    if d == today:
        return "Bugün"
    if d == today + timedelta(days=1):
        return "Yarın"
    return f"{d.strftime('%d.%m')} {GUNLER[d.weekday()]}"


def alert(o: dict, today: date) -> str:
    if o["kind"] == "elektrik":
        lines = [f"⚡ Planlı elektrik kesintisi · {config.NEIGHBORHOOD}",
                 f"📅 {day_label(o['day'], today)} {o['start']}–{o['end']}",
                 f"📍 {tidy_names(o['areas'])[:200]}"]
        if o["streets"]:
            lines.append(f"🛣 {short_streets(o['streets'])}")
    else:
        lines = [f"💧 Su kesintisi duyurusu · {config.DISTRICT.title()}", f"📍 {o['title'][:500]}"]
    lines.append(f"🔗 Kaynak: {o['url']}")
    return "\n".join(lines)


def relevant_su(o: dict) -> bool:
    """İSU Kocaeli'nin tamamını duyurur: yalnızca ilçemizi ya da mahallemizi anan metinler ilgilendirir."""
    return norm(config.DISTRICT) in norm(o["title"]) or affects_me(o)


async def fetch_sedas() -> list[dict]:
    async with client(25) as c:
        return await src.sedas(c)


async def fetch_isu() -> list[dict]:
    async with client(25) as c:
        return [o for o in await src.isu(c) if relevant_su(o)]


async def fetch_all() -> list[dict]:
    """/kesinti için anlık okuma: SEDAŞ + ilgili İSU duyuruları. Birinin hatası diğerini engellemez."""
    el, su = await asyncio.gather(fetch_sedas(), fetch_isu(), return_exceptions=True)
    result = []
    for name, res in (("SEDAŞ", el), ("İSU", su)):
        if isinstance(res, Exception):
            log.error("%s okunamadı (%s)", name, type(res).__name__)
        else:
            result += res
    return result


def still_relevant(o: dict, now: datetime) -> bool:
    """Bitmiş kesinti için uyarı gönderme (bugünse bitiş saati geçmiş olmasın)."""
    if o["kind"] == "su":
        return True
    today = now.date()
    if o["day"] > today:
        return True
    if o["day"] < today:
        return False
    try:
        hh, mm = map(int, o["end"].split(":"))
    except ValueError:
        return True
    return (hh, mm) > (now.hour, now.minute)


async def notify(items: list[dict]) -> None:
    """Mahalleyi etkileyen yeni (ya da saati değişmiş) kesintiler için herkese uyarı gönderir."""
    seen = set(kv_json("planned_seen", []))
    now = datetime.now(config.TZ)
    for o in items:
        key = f"{o['id']}|{o.get('sig', '')}"
        if key in seen:
            continue
        updated = any(k.startswith(o["id"] + "|") for k in seen)       # aynı kayıt, farklı tarih/saat
        seen.add(key)
        if affects_me(o) and still_relevant(o, now):
            await tg.send(("🔄 Güncellendi\n" if updated else "") + alert(o, now.date()))
    kv_set("planned_seen", json.dumps(sorted(seen)[-2000:]))


def store(name: str, items: list[dict]) -> None:
    kv_set(f"planned_cache_{name}", json.dumps([{**o, "day": o["day"].isoformat()} if "day" in o else o for o in items]))


async def poll(name: str, fetch, interval: int, state: dict) -> None:
    """Bir kaynağı sırası gelince okur; hata olursa aralığı ikiye katlayıp (en çok 1 saat) tekrar dener."""
    if time.time() < state["next"]:
        return
    try:
        items = await fetch()
        store(name, items)
        await notify(items)
        state.update(fails=0, next=time.time() + interval * random.uniform(0.9, 1.1))
    except Exception as e:
        state["fails"] += 1
        wait = min(interval * 2 ** state["fails"], config.POLL_BACKOFF_MAX_SEC)
        state["next"] = time.time() + wait
        log.error("%s kontrolü başarısız (%s); %d sn sonra tekrar denenecek", name, type(e).__name__, wait)


async def loop() -> None:
    sched = {"sedas": {"next": 0.0, "fails": 0}, "isu": {"next": 0.0, "fails": 0}}
    while True:
        await poll("sedas", fetch_sedas, config.SEDAS_POLL_SEC, sched["sedas"])
        await poll("isu", fetch_isu, config.ISU_POLL_SEC, sched["isu"])
        await asyncio.sleep(30)


def cached() -> list[dict]:
    out = []
    for name in ("sedas", "isu"):
        for o in kv_json(f"planned_cache_{name}", []):
            if "day" in o:
                o["day"] = date.fromisoformat(o["day"])
            out.append(o)
    return out


def section(items: list[dict] | None = None, days: int = 2) -> str:
    """Özet ve /kesinti için: bugün (+yarın) ilçedeki planlı kesintiler; mahallemi etkileyenler başta."""
    items = cached() if items is None else items
    today = datetime.now(config.TZ).date()
    el = [o for o in items if o["kind"] == "elektrik" and today <= o["day"] < today + timedelta(days=days)]
    su = [o for o in items if o["kind"] == "su"]
    title = f"🔌 Planlı kesintiler · {config.DISTRICT.title()}"
    if not el and not su:
        return f"{title}\n{'Bugün ve yarın' if days == 2 else 'Yakın tarihte'} planlı kesinti yok."
    mine = [o for o in el if affects_me(o)]
    lines = [title]
    for o in mine:
        lines.append(f"⚠️ {config.NEIGHBORHOOD}: {day_label(o['day'], today)} {o['start']}–{o['end']}")
    others = [o for o in el if o not in mine]
    limit = 4 if days == 2 else 15          # özet kısa kalsın; tamamı /kesinti'de
    for o in others[:limit]:
        lines.append(f"⚡ {day_label(o['day'], today)} {o['start']}–{o['end']} · {tidy_names(o['areas'])[:70]}")
    if len(others) > limit:
        lines.append(f"… ve {len(others) - limit} kesinti daha (/kesinti)")
    for o in su[:4]:
        lines.append(f"💧 {('⚠️ ' if affects_me(o) else '')}{o['title'][:160]}")
    if not mine and config.NEIGHBORHOOD:
        lines.append(f"✅ {config.NEIGHBORHOOD} etkilenmiyor.")
    return "\n".join(lines)


async def reply(chat_id: int) -> None:
    try:
        items = await fetch_all()
    except Exception as e:
        await tg.send(f"Kesinti bilgisi alınamadı ({type(e).__name__}).", to=[chat_id])
        return
    await tg.send(section(items, days=7) + f"\n\n🔗 Elektrik: {src.SEDAS_PAGE}\n🔗 Su: {src.ISU_PAGE}", to=[chat_id])
