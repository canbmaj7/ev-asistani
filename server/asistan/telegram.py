"""Telegram Bot API: mesaj gönderme/silme, komut menüsü, güncellemeleri çekme.

Bot token'ı URL'de olduğu için istisna metinleri günlüğe yazılmaz, yalnızca türleri.
"""
import httpx

from . import config, members
from .util import log

API = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"


async def send(text: str, to: list[int] | None = None) -> None:
    """Mesajı `to` listesine, verilmezse tüm alıcılara gönderir."""
    for chat_id in to if to is not None else members.recipients():
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{API}/sendMessage", json={
                    "chat_id": chat_id, "text": text, "disable_web_page_preview": True})
                r.raise_for_status()
            log.info("Telegram'a gönderildi (%s): %s", chat_id, text.replace("\n", " | "))
        except Exception as e:  # bildirim hatası servisi düşürmesin
            log.error("Telegram gönderilemedi (%s, %s)", chat_id, type(e).__name__)


async def delete_message(chat_id: int, message_id: int | None) -> bool:
    if not message_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{API}/deleteMessage", json={"chat_id": chat_id, "message_id": message_id})
        return r.json().get("ok", False)
    except Exception:
        return False


async def set_commands(member_cmds: list[dict], owner_cmds: list[dict]) -> None:
    """`/` yazınca çıkan menü: herkes için ve sahip için ayrı liste."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(f"{API}/setMyCommands", json={"commands": member_cmds})
            await c.post(f"{API}/setMyCommands", json={
                "commands": owner_cmds, "scope": {"type": "chat", "chat_id": config.OWNER_ID}})
    except Exception as e:
        log.error("Komut menüsü ayarlanamadı (%s)", type(e).__name__)


async def get_updates(offset: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=40) as c:
        r = await c.get(f"{API}/getUpdates", params={"timeout": 30, "offset": offset})
    return r.json().get("result", [])
