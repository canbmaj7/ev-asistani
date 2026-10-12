"""Nöbetçi eczaneler (/eczane)."""
from urllib.parse import quote_plus

from . import config
from . import telegram as tg
from .sources import pharmacy as src
from .sources.common import client


async def reply(chat_id: int) -> None:
    try:
        async with client() as c:
            day, items = await src.fetch(c)
    except Exception as e:
        await tg.send(f"Nöbetçi eczane listesi alınamadı ({type(e).__name__}).\n🔗 {config.PHARMACY_URL}", to=[chat_id])
        return
    lines = [f"💊 Nöbetçi eczaneler · {config.DISTRICT.title()}" + (f" ({day})" if day else "")]
    for e in items:
        lines.append(f"\n🏥 {e['name']}\n📍 {e['address']}\n📞 {e['phone']}\n"
                     f"🗺 https://maps.google.com/?q={quote_plus(e['name'] + ' ' + e['address'])}")
    lines.append(f"\nGitmeden önce telefonla teyit et.\n🔗 Kaynak: {config.PHARMACY_URL}")
    await tg.send("\n".join(lines), to=[chat_id])
