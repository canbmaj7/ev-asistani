"""Planlı kesintiler: mahalle eşleştirme, uyarılar ve İSU ayrıştırıcı."""
from datetime import datetime, timedelta

import pytest
from conftest import run

from asistan import config, planned
from asistan.db import kv_json
from asistan.sources import outages

TODAY = datetime.now(config.TZ).date()


@pytest.fixture(autouse=True)
def neighbourhood(monkeypatch):
    monkeypatch.setattr(config, "NEIGHBORHOOD", "Örnek Köy")


def el(i, day, areas, start="09:00", end="17:00"):
    return {"id": f"sedas-{i}", "kind": "elektrik", "day": day, "start": start, "end": end,
            "areas": areas, "streets": "1. SK.", "url": outages.SEDAS_PAGE}


def test_affects_me_turkish_spelling():
    assert planned.affects_me(el(1, TODAY, "BALÇIK, ÖRNEK KÖY, PELİTLİ"))
    assert planned.affects_me(el(1, TODAY, "ornekkoy"))
    assert not planned.affects_me(el(1, TODAY, "YAVUZ SELİM, KÖSKLÜ ÇESME"))


def test_loop_alerts_only_my_neighbourhood_once(outbox, monkeypatch):
    items = [el(1, TODAY + timedelta(days=1), "ÖRNEK KÖY, BALÇIK"), el(2, TODAY, "YENİKENT"),
             el(3, TODAY - timedelta(days=1), "ÖRNEK KÖY")]          # geçmiş: bildirilmez

    async def fake_fetch():
        return [dict(o) for o in items]
    monkeypatch.setattr(planned, "fetch_all", fake_fetch)
    monkeypatch.setattr(config, "PLANNED_POLL_SEC", 0)

    async def rounds(n):
        import asyncio
        calls = {"n": 0}
        real = asyncio.sleep

        async def sleep(_):
            calls["n"] += 1
            if calls["n"] >= n:
                raise asyncio.CancelledError
            await real(0)
        monkeypatch.setattr(planned.asyncio, "sleep", sleep)
        with pytest.raises(asyncio.CancelledError):
            await planned.loop()
    run(rounds(2))
    assert len(outbox) == 1 and outbox[0][0] == ("HERKES",)
    text = outbox.last()
    assert "Planlı elektrik kesintisi" in text and "Yarın 09:00–17:00" in text and "🔗 Kaynak: https://www.sedas.com" in text
    assert set(kv_json("planned_seen", [])) == {"sedas-1", "sedas-2", "sedas-3"}


def test_no_neighbourhood_no_alerts(monkeypatch):
    monkeypatch.setattr(config, "NEIGHBORHOOD", "")
    assert not planned.affects_me(el(1, TODAY, "ÖRNEK KÖY"))


def test_section():
    items = [el(1, TODAY, "ÖRNEK KÖY"), el(2, TODAY, "YENİKENT"), el(3, TODAY + timedelta(days=5), "ULUS")]
    text = planned.section(items)
    assert "⚠️ Örnek Köy: Bugün 09:00–17:00" in text and "YENİKENT" in text and "ULUS" not in text
    assert "etkilenmiyor" in planned.section([el(2, TODAY, "YENİKENT")])
    assert "planlı kesinti yok" in planned.section([])


ISU_EMPTY = '<h3 class="uk-card-title">SU KESİNTİLERİ</h3><div id="NoInterruptionPanel2"> Planlanmış bir su kesintisi bulunmamaktadır. </div><h3 class="uk-card-title">DUYURULAR</h3>'
ISU_ONE = ('<h3 class="uk-card-title">SU KESİNTİLERİ</h3><ul><li><a href="/sukesintileri/detay.aspx?Id=812">'
           'Gebze Örnek Köy Mahallesi su kesintisi</a></li></ul><h3 class="uk-card-title">DUYURULAR</h3>')


def test_parse_isu():
    assert outages.parse_isu(ISU_EMPTY) == []
    items = outages.parse_isu(ISU_ONE)
    assert items == [{"id": "isu-812", "kind": "su", "title": "Gebze Örnek Köy Mahallesi su kesintisi",
                      "url": "https://www.isu.gov.tr/sukesintileri/detay.aspx?Id=812"}]
    with pytest.raises(ValueError):
        outages.parse_isu("<html>yeni tasarım</html>")
    with pytest.raises(ValueError):
        outages.parse_isu('<h3 class="uk-card-title">SU KESİNTİLERİ</h3><div>beklenmeyen içerik</div>')


def test_tidy_names_dedupes_turkish_variants():
    assert planned.tidy_names("ÖRNEK KÖY, CUMHURIYET, CUMHURİYET, MIMAR SINAN, MİMAR SİNAN") == "ÖRNEK KÖY, CUMHURİYET, MİMAR SİNAN"
    assert planned.tidy_names("A,, B , A") == "A, B"


def test_short_streets_never_cuts_mid_word():
    streets = ", ".join(f"{2200 + i}. SK." for i in range(60)) + ", 2203. SK."
    out = planned.short_streets(streets, limit=80)
    assert out.endswith("sokak daha") and len(out) < 120
    assert not out.split(" …")[0].endswith(("2", "SK")) or out.split(" …")[0].endswith("SK.")
    assert planned.short_streets("1. SK., 2. SK., 1. SK.") == "1. SK., 2. SK."          # tekrarlar atıldı


def test_alert_uses_tidy_text(monkeypatch):
    o = el(9, TODAY, "ÖRNEK KÖY, CUMHURIYET, CUMHURİYET")
    o["streets"] = ", ".join(f"{3000 + i}. SK." for i in range(80))
    text = planned.alert(o, TODAY)
    assert "CUMHURIYET" not in text and "CUMHURİYET" in text and "sokak daha" in text
    assert max(len(line) for line in text.splitlines()) < 260
