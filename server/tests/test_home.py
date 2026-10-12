"""Nabız, kesinti tespiti ve canlı ölçüm."""
import asyncio
import time

from conftest import run

from asistan import home
from asistan.db import db

H = {"X-Token": "beat-token"}


def last_outage():
    with db() as con:
        r = con.execute("SELECT * FROM outages ORDER BY id DESC LIMIT 1").fetchone()
    return r["kind"], r["ended"] - r["started"]


def test_token_required(client):
    async def go():
        async with client as c:
            assert (await c.post("/beat", json={"boot_id": "x"})).status_code == 401
            assert (await c.post("/wait")).status_code == 401
            assert (await c.post("/beat", headers=H, json={"boot_id": "x"})).status_code == 200
    run(go())


def test_short_power_cut_excludes_boot_time(outbox):
    """Son nabız 100 sn önce, Pi 40 sn önce açıldı → kesinti ~60 sn."""
    home.state.update(last_beat=time.time() - 100, boot_id="eski")
    run(home.handle_beat(home.Beat(boot_id="yeni", uptime=40)))
    kind, dur = last_outage()
    assert kind == "elektrik" and 55 <= dur <= 65
    assert "Kısa elektrik kesintisi" in outbox.last() and "1 dk" in outbox.last()
    assert outbox[-1][0] == ("HERKES",)


def test_long_power_cut_end(outbox):
    now = time.time()
    home.state.update(last_beat=now - 400, boot_id="a", down_since=now - 400)
    run(home.handle_beat(home.Beat(boot_id="b", uptime=50)))
    kind, dur = last_outage()
    assert kind == "elektrik" and 345 <= dur <= 355
    assert "Elektrik kesintisi bitti" in outbox.last()
    assert home.state["down_since"] is None


def test_internet_outage_same_boot(outbox):
    now = time.time()
    home.state.update(last_beat=now - 300, boot_id="a", down_since=now - 300)
    run(home.handle_beat(home.Beat(boot_id="a", uptime=9999)))
    assert last_outage()[0] == "internet"
    assert "İnternet kesintisi bitti" in outbox.last()


def test_short_internet_events_reported(outbox):
    home.state.update(last_beat=time.time() - 30, boot_id="a")
    run(home.handle_beat(home.Beat(boot_id="a", uptime=500, net_events=[{"dur": 45, "ago": 10}])))
    assert last_outage()[0] == "internet"
    assert "Kısa internet kesintisi" in outbox.last()


def test_monitor_alarm(outbox, monkeypatch):
    home.state.update(last_beat=time.time() - 500, boot_id="a")
    monkeypatch.setattr(home, "STARTED_AT", time.time() - 1000)

    async def one_round():
        real_sleep = asyncio.sleep
        calls = {"n": 0}

        async def fast_sleep(_):
            calls["n"] += 1
            if calls["n"] > 1:
                raise asyncio.CancelledError
            await real_sleep(0)
        monkeypatch.setattr(home.asyncio, "sleep", fast_sleep)
        try:
            await home.monitor_loop()
        except asyncio.CancelledError:
            pass
    run(one_round())
    assert "nabız alınamıyor" in outbox.last()
    assert home.state["down_since"] is not None


def test_live_status_roundtrip(client, outbox, monkeypatch):
    """/durum: sunucu Pi'yi uyandırır, Pi canlı nabızla yanıt verir."""
    monkeypatch.setattr(home.config, "LIVE_TIMEOUT", 3)
    home.state.update(last_beat=time.time(), boot_id="a")

    async def go():
        async with client as c:
            async def pi():
                r = await c.post("/wait", headers=H, timeout=10)
                assert r.json()["ping"] is True
                await c.post("/beat", headers=H, json={"boot_id": "a", "uptime": 4000, "live": True,
                              "info": {"latency_ms": 23, "temp": 42.4, "load": 0.1, "mem_mb": 560, "wifi_dbm": -52}})
            task = asyncio.create_task(pi())
            await asyncio.sleep(0.1)
            text = await home.live_status()
            await task
            return text
    text = run(go())
    assert text.startswith("🟢 Her şey yolunda") and "23 ms" in text and "-52 dBm" in text and "1 sa 6 dk" in text


def test_live_status_timeout(monkeypatch):
    monkeypatch.setattr(home.config, "LIVE_TIMEOUT", 1)
    home.state.update(last_beat=time.time(), boot_id="a")
    assert run(home.live_status()).startswith("🟠")
    assert not home.ping_event.is_set() and not home.live_waiters


def test_live_status_when_down():
    home.state.update(last_beat=time.time() - 300, boot_id="a", down_since=time.time() - 300)
    assert run(home.live_status()).startswith("🔴")
