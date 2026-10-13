"""Ağ bekçisi: Pi'nin ağ taramasını işler, yeni cihazları sahibe bildirir."""
import re
import time

from . import config
from . import telegram as tg
from .db import db
from .util import clean

MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


async def handle_scan(devs: list[dict], now: float, net: str = "") -> None:
    """Her Wi-Fi ağı için ilk tarama başlangıç listesi sayılır (ağ değişince yeni cihaz yağmuru olmaz);
    sonradan o ağda görülen yeni MAC'ler bildirilir."""
    seen = {}
    for d in devs[:150]:
        mac = clean(d.get("mac")).lower()
        if MAC_RE.match(mac) and mac != "00:00:00:00:00:00":
            seen[mac] = (clean(d.get("ip"), 45), clean(d.get("host")))
    if not seen:
        return
    with db() as con:
        known = {r["mac"] for r in con.execute("SELECT mac FROM devices WHERE net = ?", (net,))}
        everyone = {r["mac"] for r in con.execute("SELECT mac FROM devices")}
        new = []
        for mac, (ip, host) in seen.items():
            if mac in everyone:
                con.execute("UPDATE devices SET ip = ?, host = COALESCE(NULLIF(?, ''), host), last_seen = ?, net = ? WHERE mac = ?",
                            (ip, host, now, net, mac))
            else:
                con.execute("INSERT INTO devices (mac, ip, host, first_seen, last_seen, net) VALUES (?, ?, ?, ?, ?, ?)",
                            (mac, ip, host, now, now, net))
            if mac not in known:
                new.append((mac, ip, host))
    if not known:
        label = f" ({net})" if net else ""
        await tg.send(
            f"📋 Ağda{label} {len(seen)} cihaz bulundu ve başlangıç listesine kaydedildi.\n"
            "Bundan sonra listede olmayan yeni bir cihaz bağlanınca haber vereceğim.\n"
            "Liste: /cihazlar · İsim vermek için: /cihaz SON4 isim", to=[config.OWNER_ID])
        return
    for mac, ip, host in new:
        await tg.send(
            "🆕 Ağda yeni bir cihaz göründü\n"
            f"IP: {ip or '?'}  ·  Ad: {host or 'bilinmiyor'}\n"
            f"MAC: {mac}\n"
            f"Tanıyorsan isim ver: /cihaz {mac.replace(':', '')[-4:]} isim", to=[config.OWNER_ID])


def listing(limit: int = 100) -> list[dict]:
    now = time.time()
    with db() as con:
        rows = con.execute("SELECT mac, ip, host, name, last_seen FROM devices ORDER BY last_seen DESC LIMIT ?",
                           (limit,)).fetchall()
    return [{**dict(r), "online": now - r["last_seen"] < config.ONLINE_SEC} for r in rows]


def text() -> str:
    rows = listing(60)
    if not rows:
        return "Henüz tarama sonucu yok. (Pi her 5 dakikada bir tarar.)"
    lines = [f"📡 Ağ cihazları ({sum(r['online'] for r in rows)} çevrimiçi / {len(rows)} kayıtlı)"]
    for r in rows:
        lines.append(f"{'🟢' if r['online'] else '⚪'} {r['name'] or r['host'] or 'adsız'} · {r['ip'] or '?'}"
                     f" · …{r['mac'].replace(':', '')[-4:]}")
    return "\n".join(lines)


def rename(arg: str) -> str:
    token, _, name = arg.strip().partition(" ")
    token = token.lower().replace(":", "")
    name = clean(name.strip(), 40)
    if not token or not name:
        return "Kullanım: /cihaz SON4 isim   (SON4: /cihazlar listesindeki …xxxx)"
    with db() as con:
        rows = con.execute("SELECT mac FROM devices WHERE replace(mac, ':', '') LIKE ?", ("%" + token,)).fetchall()
        if len(rows) != 1:
            return "Böyle bir cihaz yok." if not rows else "Birden çok cihaz eşleşti, daha uzun yaz."
        con.execute("UPDATE devices SET name = ? WHERE mac = ?", (name, rows[0]["mac"]))
    return f"✅ Kaydedildi: {name}"
