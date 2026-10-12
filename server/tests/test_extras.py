"""Hava uyarıları, ezan/Ramazan, eczane, hız testi ve bakım modu."""
import asyncio
import time
from datetime import datetime

import pytest
from conftest import MEMBER, OWNER, run

from asistan import config, home, prayer, speed, weather_alerts
from asistan.db import pref_set
from asistan.sources import pharmacy
from asistan.sources import prayer as prayer_src

H = {"X-Token": "beat-token"}


def forecast(rain=(0, 0, 0, 0, 0, 0, 0, 0), gusts=(10,) * 14, codes=(1,) * 14, precip=(0,) * 14, temps=(10,) * 14):
    return {"minutely_15": {"precipitation": list(rain)},
            "hourly": {"time": [f"2026-10-13T{h:02d}:00" for h in range(14)], "wind_gusts_10m": list(gusts),
                       "weather_code": list(codes), "precipitation": list(precip), "temperature_2m": list(temps)}}


NOON = datetime(2026, 10, 13, 12, 0, tzinfo=config.TZ)


def rules(f, now=NOON):
    return {r: severe for r, _, severe in weather_alerts.evaluate(f, now)}


def test_weather_rules():
    assert rules(forecast()) == {}
    assert rules(forecast(rain=(0, 0, 0.5, 1, 1, 0, 0, 0))) == {"yagmur": False}
    assert rules(forecast(rain=(0.4, 0.5, 1, 1, 1, 0, 0, 0))) == {}           # zaten yağıyor
    assert rules(forecast(gusts=(65,) + (10,) * 13)) == {"ruzgar": False}
    assert rules(forecast(gusts=(85,) + (10,) * 13)) == {"ruzgar": True}
    assert rules(forecast(codes=(95,) + (1,) * 13))["firtina"] is False      # gece uyandırmaz
    assert rules(forecast(precip=(5,) * 6 + (0,) * 8))["saganak"] is True
    assert rules(forecast(temps=(-2,) * 14), datetime(2026, 1, 13, 19, 0, tzinfo=config.TZ)) == {"don": False}
    assert "don" not in rules(forecast(temps=(-2,) * 14))                     # yalnızca akşam


def test_weather_loop_quiet_hours_and_cooldown(outbox, monkeypatch):
    async def fake_forecast(c):
        return forecast(rain=(0, 0, 0.5, 1, 1, 0, 0, 0), codes=(95,) + (1,) * 13, precip=(5,) * 6 + (0,) * 8)
    monkeypatch.setattr(weather_alerts.weather, "forecast", fake_forecast)
    monkeypatch.setattr(config, "WEATHER_POLL_SEC", 0)
    monkeypatch.setattr(weather_alerts, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: datetime(2026, 10, 13, 2, 0, tzinfo=config.TZ))}))
    calls = {"n": 0}
    real = asyncio.sleep

    async def sleep(_):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise asyncio.CancelledError
        await real(0)
    monkeypatch.setattr(weather_alerts.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        run(weather_alerts.loop())
    texts = outbox.texts()
    assert len(texts) == 1 and "Kuvvetli yağış" in texts[0] and "open-meteo" in texts[0]   # gece: yalnızca ciddi, bir kez


DAY = {"MiladiTarihKisa": "13.10.2026", "Imsak": "05:40", "Gunes": "07:04", "Ogle": "12:54", "Ikindi": "16:00",
       "Aksam": "18:33", "Yatsi": "19:52", "HicriTarihUzun": "2 Ramazan 1448"}


def test_prayer_helpers():
    assert prayer_src.line(DAY).startswith("🕌 İmsak 05:40")
    assert prayer_src.is_ramadan(DAY) and not prayer_src.is_ramadan({**DAY, "HicriTarihUzun": "2 Şevval 1448"})
    assert prayer.next_prayer(DAY, datetime(2026, 10, 13, 13, 0, tzinfo=config.TZ))[:2] == ("İkindi", "16:00")
    assert prayer.next_prayer(DAY, datetime(2026, 10, 13, 23, 0, tzinfo=config.TZ)) is None
    at = lambda h, m: datetime(2026, 10, 13, h, m, tzinfo=config.TZ)  # noqa: E731
    assert [k for k, _ in prayer.due_reminders(DAY, at(4, 56))] == ["Imsak-2026-10-13"]
    assert [k for k, _ in prayer.due_reminders(DAY, at(18, 20))] == ["Aksam-2026-10-13"]
    assert prayer.due_reminders(DAY, at(12, 0)) == []


def test_ramadan_opt_in(outbox, member, monkeypatch):
    run(prayer.toggle_ramadan(MEMBER))
    assert "açıldı" in outbox.last()

    async def fake_days():
        return [DAY]
    monkeypatch.setattr(prayer, "days", fake_days)
    monkeypatch.setattr(prayer, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: datetime(2026, 10, 13, 18, 20, tzinfo=config.TZ))}))
    real = asyncio.sleep
    n = {"i": 0}

    async def sleep(_):
        n["i"] += 1
        if n["i"] >= 2:
            raise asyncio.CancelledError
        await real(0)
    monkeypatch.setattr(prayer.asyncio, "sleep", sleep)
    outbox.clear()
    with pytest.raises(asyncio.CancelledError):
        run(prayer.ramadan_loop())
    assert outbox == [((MEMBER,), "🍽 İftara 15 dk kaldı (Akşam 18:33).")]     # yalnızca açan kişiye, bir kez


PHARMACY_HTML = """<p>12 Ekim 2026 Pazartesi</p>
<li class="overflow-hidden w-full flex"><p class="block w-full font-bold">Enes Eczanesi</p>
<p><span class="font-bold">Adres: </span>Beylikbağı Mah. No:6/B Gebze</p><p><span class="font-bold">Telefon: </span>0(262)743-43-10</p></li>"""


def test_pharmacy_parse():
    day, items = pharmacy.parse(PHARMACY_HTML)
    assert day == "12 Ekim 2026 Pazartesi"
    assert items == [{"name": "Enes Eczanesi", "address": "Beylikbağı Mah. No:6/B Gebze", "phone": "0(262)743-43-10"}]
    with pytest.raises(ValueError):
        pharmacy.parse("<html></html>")


def test_speedtest_roundtrip(client, outbox, monkeypatch):
    """/hiz test: sunucu komutu kanala koyar, Pi ölçüp sonucu nabızla yollar, kayıt tutulur."""
    monkeypatch.setattr(config, "SPEEDTEST_TIMEOUT", 3)
    home.state.update(last_beat=time.time(), boot_id="a")

    async def go():
        async with client as c:
            async def pi():
                r = (await c.post("/wait", headers=H, timeout=10)).json()
                assert r == {"ping": True, "cmd": "speedtest"}
                await c.post("/beat", headers=H, json={"boot_id": "a", "uptime": 100, "live": True, "speedtest":
                              {"down_mbps": 41.5, "up_mbps": 9.2, "ping_ms": 18, "trigger": "elle"}})
            t = asyncio.create_task(pi())
            await asyncio.sleep(0.05)
            r = await home.request_speedtest()
            await t
            return r
    r = run(go())
    assert r["down_mbps"] == 41.5
    h = speed.history()
    assert len(h) == 1 and h[0]["trigger"] == "elle"
    assert "41.5" in speed.text() and "↓ 41.5" in speed.summary_line()


def test_nightly_speedtest_saved_from_beat():
    run(home.handle_beat(home.Beat(boot_id="a", uptime=10, speedtest={"down_mbps": 30, "up_mbps": 5, "ping_ms": 20})))
    assert speed.history()[0]["trigger"] == "gece"


def test_maintenance_mutes_outage_messages(client, outbox):
    async def go():
        async with client as c:
            assert (await c.post("/maintenance", json={"minutes": 10})).status_code == 401
            assert (await c.post("/maintenance", headers=H, json={"minutes": 10})).status_code == 200
    run(go())
    home.state.update(last_beat=time.time() - 100, boot_id="eski")
    run(home.handle_beat(home.Beat(boot_id="yeni", uptime=40)))      # yeniden başlatma
    assert not outbox
    from asistan.db import db
    with db() as con:
        assert con.execute("SELECT COUNT(*) FROM outages").fetchone()[0] == 0
