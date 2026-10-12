"""Davet koduyla üyelik: sahip /davet ile tek kullanımlık kod üretir, kişi /katil KOD yazar."""
import secrets
import time

from . import config, members
from . import telegram as tg

TTL = 600
codes: dict[str, float] = {}       # kod → son geçerlilik
bad_attempts: list[float] = []     # hatalı /katil denemeleri (kaba kuvvete karşı)


def new_code() -> str:
    now = time.time()
    for k in [k for k, v in codes.items() if v < now]:
        del codes[k]
    code = secrets.token_hex(3).upper()
    codes[code] = now + TTL
    return code


def display_name(msg: dict) -> str:
    u = msg.get("from", {})
    return (u.get("first_name") or u.get("username") or "Üye")[:40]


async def join(chat_id: int, msg: dict, arg: str, welcome: str) -> None:
    now = time.time()
    bad_attempts[:] = [t for t in bad_attempts if now - t < TTL]
    if len(bad_attempts) >= 10:
        return                                  # çok fazla hatalı deneme: sessizce yoksay
    if members.is_member(chat_id):
        await tg.send("Zaten üyesin. Komutlar için /yardim", to=[chat_id])
        return
    code = arg.strip().upper()
    if codes.get(code, 0) < now:
        bad_attempts.append(now)
        await tg.send("Kod geçersiz ya da süresi dolmuş.", to=[chat_id])
        return
    del codes[code]                             # tek kullanımlık
    name = display_name(msg)
    members.add(chat_id, name)
    await tg.send(welcome, to=[chat_id])
    await tg.send(f"👤 {name} bildirimlere katıldı.", to=[config.OWNER_ID])
