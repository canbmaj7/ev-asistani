"""Ortak yardımcılar: biçimlendirme, arka plan görevleri, günlük."""
import asyncio
import logging
import re
from datetime import datetime

from . import config

log = logging.getLogger("asistan")
_background: set = set()


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, config.TZ).strftime("%d.%m.%Y %H:%M:%S")


def fmt_dur(sec: float) -> str:
    sec = int(max(sec, 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} sa {m} dk"
    if m:
        return f"{m} dk {s} sn"
    return f"{s} sn"


def clean(v, n: int = 60) -> str:
    """Dışarıdan gelen metni kırp, kontrol karakterlerini at."""
    return re.sub(r"[\x00-\x1f\x7f]", "", str(v or ""))[:n]


def spawn(coro) -> None:
    """Uzun süren işi arka planda başlat (Telegram polling'i bloklamasın)."""
    t = asyncio.create_task(coro)
    _background.add(t)
    t.add_done_callback(_background.discard)


_TR = str.maketrans({"ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ç": "c", "Ç": "c",
                     "ö": "o", "Ö": "o", "ü": "u", "Ü": "u", "ğ": "g", "Ğ": "g", "â": "a", "î": "i", "û": "u"})


def norm(s: str) -> str:
    """Türkçe karşılaştırma için: 'KÖSKLÜ ÇESME' == 'Köşklüçeşme' → 'koskluceme'… boşluksuz, aksansız."""
    return "".join(ch for ch in (s or "").translate(_TR).lower() if ch.isalnum())


def in_quiet_hours(hour: int, quiet: tuple[int, int]) -> bool:
    start, end = quiet
    return hour >= start or hour < end if start > end else start <= hour < end
