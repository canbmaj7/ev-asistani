"""İnternet hız testi kayıtları: Pi her gece ölçer (ve /hiz test ile istenince), sunucu saklar."""
import time

from . import config
from .db import db
from .util import fmt_time


def record(r: dict, trigger: str) -> None:
    with db() as con:
        con.execute("INSERT INTO speedtests (at, down_mbps, up_mbps, ping_ms, trigger) VALUES (?, ?, ?, ?, ?)",
                    (time.time(), r.get("down_mbps"), r.get("up_mbps"), r.get("ping_ms"), trigger))


def history(limit: int = 30) -> list[dict]:
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM speedtests ORDER BY at DESC LIMIT ?", (limit,))]


def fmt(r: dict) -> str:
    def v(x):
        return f"{x:.1f}" if isinstance(x, (int, float)) else "?"
    return f"↓ {v(r.get('down_mbps'))} Mbps · ↑ {v(r.get('up_mbps'))} Mbps · ping {round(r['ping_ms']) if r.get('ping_ms') else '?'} ms"


def week_stats() -> dict | None:
    with db() as con:
        row = con.execute("SELECT AVG(down_mbps) d, MIN(down_mbps) mn, MAX(down_mbps) mx, AVG(up_mbps) u, COUNT(*) n "
                          "FROM speedtests WHERE at > ? AND down_mbps IS NOT NULL", (time.time() - 7 * 86400,)).fetchone()
    return dict(row) if row and row["n"] else None


def summary_line() -> str | None:
    h = history(1)
    if not h or time.time() - h[0]["at"] > 36 * 3600:
        return None
    w = week_stats()
    avg = f" (7 gün ort. ↓ {w['d']:.1f})" if w and w["n"] > 1 else ""
    return f"📶 Hız testi ({fmt_time(h[0]['at'])[11:16]}): {fmt(h[0])}{avg}"


def text() -> str:
    h = history(10)
    if not h:
        return "Henüz hız testi kaydı yok. Pi her gece 03:30'da ölçer; şimdi ölçmek için: /hiz test"
    lines = ["📶 Hız testi kayıtları"]
    for r in h:
        lines.append(f"{fmt_time(r['at'])[:16]} · {fmt(r)}" + (" (elle)" if r["trigger"] != "gece" else ""))
    if w := week_stats():
        lines.append(f"\n7 gün: ort. ↓ {w['d']:.1f} (en düşük {w['mn']:.1f}, en yüksek {w['mx']:.1f}) · ↑ {w['u']:.1f} Mbps · {w['n']} ölçüm")
    lines.append("🔗 Ölçüm: speed.cloudflare.com")
    return "\n".join(lines)
