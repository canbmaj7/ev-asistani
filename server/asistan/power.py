"""Pi'nin güç sağlığı: reset sebebi (get_rsts), düşük voltaj bayrakları (get_throttled) ve uyarılar."""
import time

from . import config, stats
from . import telegram as tg
from .db import kv_get, kv_set

RESET_ALERT_COUNT = 3        # 24 saatte bu kadar Pi resetinde sahibe tek bir uyarı gider
RESET_ALERT_WINDOW = 86400
RSTS = {0x1000: "güç gitti (power-on reset)", 0x20: "donanım watchdog'u", 0x400: "yazılım reseti", 0x200: "yazılım reseti", 0x100: "yazılım reseti"}
# get_throttled bitleri: 0-3 şu an, 16-19 açılıştan beri
THROTTLE = {0: "düşük voltaj", 1: "frekans kısıldı", 2: "CPU kısıldı", 3: "ısı sınırı"}


def rsts_label(rsts: int | None) -> str:
    if rsts is None:
        return ""
    return RSTS.get(rsts, f"reset kaydı 0x{rsts:x}")


def throttle_flags(v: int | None) -> tuple[list[str], list[str]]:
    """(şu an, açılıştan beri) etkin sorunların adları."""
    if v is None:
        return [], []
    return ([n for b, n in THROTTLE.items() if v >> b & 1], [n for b, n in THROTTLE.items() if v >> (b + 16) & 1])


def power_text(info: dict) -> str:
    """Panel/durum için tek satır."""
    now, ever = throttle_flags(info.get("throttled"))
    if info.get("throttled") is None:
        return "—"
    if now:
        return "⚠️ şu an: " + ", ".join(now)
    if ever:
        return "açılıştan beri: " + ", ".join(ever)
    return "sorun yok"


def reset_note(info: dict) -> str:
    return rsts_label(info.get("rsts")) or ""


async def check_info(info: dict, boot_id: str, now: float) -> None:
    """Düşük voltaj görüldüyse (açılış başına bir kez) sahibe haber verir."""
    v = info.get("throttled")
    if v is None or not (v & 0x10001):
        return
    if kv_get("uv_alert_boot") == boot_id:
        return
    kv_set("uv_alert_boot", boot_id)
    await tg.send("⚠️ Pi düşük voltaj uyarısı aldı.\nAdaptör, kablo veya priz yetersiz olabilir; "
                  "bu yüzden Pi ara sıra resetleniyor olabilir.", to=[config.OWNER_ID])


async def after_reset(info: dict, now: float) -> None:
    """Pi resetlendi (elektrik kesintisi değil): sık tekrar ediyorsa sahibe tek uyarı gönderir."""
    n = stats.recent_count("reset", now - RESET_ALERT_WINDOW)
    if n < RESET_ALERT_COUNT or now - float(kv_get("reset_alert_at") or 0) < RESET_ALERT_WINDOW:
        return
    kv_set("reset_alert_at", now)
    cause = reset_note(info)
    await tg.send(f"🔄 Pi son 24 saatte {n} kez resetlendi (evde elektrik/internet vardı).\n"
                  + (f"Son sebep: {cause}.\n" if cause else "")
                  + "Büyük olasılıkla Pi'nin adaptörü, kablosu ya da prizi. Mümkünse başka adaptör/priz dene.",
                  to=[config.OWNER_ID])
