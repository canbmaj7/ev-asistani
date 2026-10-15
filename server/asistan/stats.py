"""Kesinti kayıtları ve istatistikleri (/gecmis, /rapor, panel grafiği)."""
import time
from datetime import datetime, timedelta

from . import config
from .db import db, kv_get
from .util import fmt_dur, fmt_time


ICONS = {"elektrik": "⚡", "internet": "🌐", "reset": "🔄"}   # reset: Pi yeniden başladı ama evde elektrik/internet vardı


def record_outage(kind: str, started: float, ended: float) -> None:
    with db() as con:
        con.execute("INSERT INTO outages (kind, started, ended) VALUES (?, ?, ?)", (kind, started, ended))


def recent_count(kind: str, since: float) -> int:
    with db() as con:
        return con.execute("SELECT COUNT(*) FROM outages WHERE kind = ? AND started > ?", (kind, since)).fetchone()[0]


def outage_stats(days: int) -> dict:
    now = time.time()
    lo = now - days * 86400
    since = float(kv_get("since") or now)
    span = max(min(now - lo, now - since), 1)
    with db() as con:
        rows = con.execute("SELECT kind, started, ended FROM outages WHERE ended > ?", (lo,)).fetchall()
    out = {"span": span, "total": 0.0, "longest": None, "kinds": {}}
    for r in rows:
        dur = r["ended"] - max(r["started"], lo)
        k = out["kinds"].setdefault(r["kind"], {"n": 0, "sec": 0.0})
        k["n"] += 1
        k["sec"] += dur
        if r["kind"] == "reset":        # Pi'nin kendi resetleri erişilebilirliği düşürmez
            continue
        out["total"] += dur
        if out["longest"] is None or dur > out["longest"][0]:
            out["longest"] = (dur, r["started"])
    out["avail"] = max(0.0, 1 - out["total"] / span)
    return out


def report_text(days_list=(7, 30)) -> str:
    parts = []
    for days in days_list:
        st = outage_stats(days)
        title = f"📊 Son {days} gün"
        if st["span"] < days * 86400:
            title += f" (kayıt {fmt_dur(st['span'])}lik)" if st["span"] < 3600 else f" (kayıt {st['span'] / 86400:.1f} günlük)"
        lines = [title]
        if not st["kinds"]:
            lines.append("Hiç kesinti olmadı 🎉")
        for kind, icon, label in (("elektrik", "⚡", "Elektrik"), ("internet", "🌐", "İnternet"), ("reset", "🔄", "Pi resetlendi")):
            if k := st["kinds"].get(kind):
                lines.append(f"{icon} {label}: {k['n']} kesinti · toplam {fmt_dur(k['sec'])}")
        if st["longest"]:
            lines.append(f"⏳ En uzun: {fmt_dur(st['longest'][0])} ({fmt_time(st['longest'][1])[:16]})")
        lines.append("✅ Erişilebilirlik: %" + f"{st['avail'] * 100:.2f}".replace(".", ","))
        parts.append("\n".join(lines))
    if since := kv_get("since"):
        parts.append(f"Kayıt başlangıcı: {fmt_time(float(since))[:16]}")
    return "\n\n".join(parts)


def history_text() -> str:
    with db() as con:
        rows = con.execute("SELECT kind, started, ended FROM outages ORDER BY id DESC LIMIT 10").fetchall()
    if not rows:
        return "Kayıtlı kesinti yok."
    return "Son kesintiler:\n" + "\n".join(
        f"{ICONS.get(r['kind'], '🌐')} {fmt_time(r['started'])} — {fmt_dur(r['ended'] - r['started'])}"
        for r in rows)


def daily_downtime(days: int = 30) -> list[dict]:
    """Son N günün günlük kesinti süresi (sn); gece yarısını aşan kesintiler bölünür."""
    today = datetime.now(config.TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    first = today - timedelta(days=days - 1)
    buckets = {(first + timedelta(days=i)).strftime("%d.%m"): 0.0 for i in range(days)}
    with db() as con:
        rows = con.execute("SELECT started, ended FROM outages WHERE ended > ?", (first.timestamp(),)).fetchall()
    for r in rows:
        t = datetime.fromtimestamp(max(r["started"], first.timestamp()), config.TZ)
        end = datetime.fromtimestamp(r["ended"], config.TZ)
        while t < end:
            nxt = t.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            if (key := t.strftime("%d.%m")) in buckets:
                buckets[key] += (min(nxt, end) - t).total_seconds()
            t = nxt
    return [{"day": k, "sec": round(v)} for k, v in buckets.items()]
