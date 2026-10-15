"""Sabah özeti (kişiye özel) ve deprem uyarısı."""
import asyncio
from datetime import datetime

import pytest
from conftest import MEMBER, OWNER, run

from asistan import calendars, config, quakes, summary
from asistan.db import db, kv_json
from asistan.sources import calendar as cal_src
from asistan.sources import quakes as afad
from asistan.sources import rates, server, weather

D = lambda h, m: datetime(2026, 10, 13, h, m, tzinfo=config.TZ)  # noqa: E731


def test_due():
    assert not summary.due(D(5, 59), None)
    assert summary.due(D(6, 0), None)
    assert not summary.due(D(6, 30), "2026-10-13")
    assert summary.due(D(7, 30), "2026-10-12")          # geç açılış telafisi
    assert not summary.due(D(15, 0), None)              # öğleden sonra sabah özeti gelmesin


@pytest.fixture
def fake_sources(monkeypatch):
    async def w(c): return "⛅ Hava · Gebze"
    async def r(c, st): st["rates"] = {"tarih": "x"}; return "💱 Kur"
    async def q(c, hours=24, min_mag=None): return []
    async def s(c, urls): return "🖥 Sunucu"
    async def cal(c, url, day): return "📅 Bugün\n09:00 · Ders"
    monkeypatch.setattr(weather, "section", w)
    monkeypatch.setattr(rates, "section", r)
    monkeypatch.setattr(afad, "fetch", q)
    monkeypatch.setattr(server, "section", s)
    monkeypatch.setattr(cal_src, "section", cal)

    async def aq(c): return "🌫 Hava kalitesi: İyi"
    monkeypatch.setattr(weather, "air_quality", aq)


def test_personal_summaries(fake_sources, member):
    with db() as con:
        con.execute("INSERT INTO calendars VALUES (?, ?, 0)", (member, "https://x/a.ics"))
    owner, mem = run(summary.build(OWNER)), run(summary.build(member))
    assert "🖥 Sunucu: her şey yolunda" in owner and "📅" not in owner          # sahip takvim bağlamadı
    assert "📅 Bugün" in mem and "🖥 Sunucu" not in mem
    for text in (owner, mem):
        assert "Günaydın" in text and "⛅ Hava" in text and "💱 Kur" in text and "🏠 Ev" in text
        assert "Deprem" not in text and "Kaynaklar" not in text and "İmsak" not in text   # özet kısa kalır
        assert "Hava kalitesi" not in text                  # iyi hava yazılmaz
    assert kv_json("ozet_state", {})["rates"]                 # kur geçmişi kaydedildi


def test_failed_section_does_not_break(fake_sources, monkeypatch):
    async def broken(c): raise TimeoutError
    monkeypatch.setattr(weather, "section", broken)
    text = run(summary.build(OWNER))
    assert "Hava: alınamadı (TimeoutError)" in text and "💱 Kur" in text


def test_daily_send_to_everyone(fake_sources, member, outbox, monkeypatch):
    monkeypatch.setattr(summary, "due", lambda now, last: True)

    async def one_round():
        t = asyncio.create_task(summary.loop())
        await asyncio.sleep(0.5)
        t.cancel()
    run(one_round())
    by_chat = {to[0]: text for to, text in outbox}
    assert set(by_chat) == {OWNER, MEMBER}
    assert "🖥 Sunucu" in by_chat[OWNER] and "🖥 Sunucu" not in by_chat[MEMBER]


def test_quake_filter():
    assert afad.in_turkey({"country": "Türkiye", "location": "Saimbeyli (Adana)"})
    assert afad.in_turkey({"country": "Türkiye", "location": "Marmara Denizi - [60.1 km] Silivri"})
    assert not afad.in_turkey({"country": "Türkiye", "location": "Ege Denizi - [165.21 km] Datça (Muğla)"})
    assert not afad.in_turkey({"country": "Yunanistan", "location": "Girit"})
    assert afad.place({"location": "Marmara Denizi - [12.5 km] Silivri (İstanbul)"}) == "Marmara Denizi Silivri (İstanbul)"


def test_quake_loop_first_round_silent_then_broadcast(outbox, member, monkeypatch):
    batches = [[{"eventID": "5"}], [{"eventID": "6"}, {"eventID": "5"}]]

    async def fake_fetch(hours):
        if not batches:
            raise asyncio.CancelledError
        return batches.pop(0)
    monkeypatch.setattr(quakes, "fetch", fake_fetch)
    monkeypatch.setattr(afad, "alert", lambda e: f"DEPREM {e['eventID']}")
    monkeypatch.setattr(config, "QUAKE_POLL_SEC", 0)
    with pytest.raises(asyncio.CancelledError):
        run(quakes.loop())
    assert outbox == [(("HERKES",), "DEPREM 6")]               # herkese, yalnızca yeni olan
    assert kv_json("seen_quakes", []) == ["5", "6"]
