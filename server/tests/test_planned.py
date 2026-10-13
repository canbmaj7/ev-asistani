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


# İSU ana sayfasındaki gerçek bölümün yapısı (boş hali, siteden alındığı gibi)
ISU_EMPTY = """<div class="watercuts-container"><div class="uk-flex"><div style="height:110px;"><div style="overflow-y:auto">
<div id="NoInterruptionPanel">

    Planlanmış bir su kesintisi bulunmamaktadır.

</div>
<div class="uk-position-relative uk-visible-toggle uk-light uk-slideshow" id="interruption-slider">
    <ul class="uk-slideshow-items" style="height: 138px;">

    </ul>
</div></div></div></div></div>"""


def isu_with(*items):
    lis = "".join(f"<li>{i}</li>" for i in items)
    return ISU_EMPTY.replace("</ul>", lis + "</ul>").replace("Planlanmış bir su kesintisi bulunmamaktadır.", "")


def test_parse_isu_empty_state_from_real_page():
    assert outages.parse_isu(ISU_EMPTY) == []


def test_parse_isu_items():
    page = isu_with(
        '<font color="#fff">Gebze Örnek Köy ve Yavuz Mahallelerinde 14.10.2026 09:00-16:00 arası şebeke çalışması nedeniyle su kesilecektir.</font>',
        '<a href="/duyurular/detay.aspx?Id=1">İzmit Yahyakaptan Mahallesi arıza</a>')
    items = outages.parse_isu(page)
    assert len(items) == 2 and all(i["kind"] == "su" for i in items)
    assert items[0]["title"].startswith("Gebze Örnek Köy ve Yavuz") and "<" not in items[0]["title"]
    assert items[0]["url"] == outages.ISU_PAGE                                   # bağlantı yoksa ana sayfa
    assert items[1]["url"] == "https://www.isu.gov.tr/duyurular/detay.aspx?Id=1"
    assert items[0]["id"] != items[1]["id"] and items[0]["id"] == outages.parse_isu(page)[0]["id"]   # kararlı kimlik


def test_parse_isu_fails_loudly_when_page_changes():
    with pytest.raises(ValueError):
        outages.parse_isu("<html>yeni tasarım</html>")
    with pytest.raises(ValueError):                         # bölüm var ama ne kesinti ne 'yok' metni
        outages.parse_isu(ISU_EMPTY.replace("Planlanmış bir su kesintisi bulunmamaktadır.", "Bakımdayız"))


def test_isu_relevance_and_alert(outbox, monkeypatch):
    mine = {"id": "isu-1", "kind": "su", "title": "Gebze Örnek Köy Mahallesi'nde su kesintisi 14.10 09:00-16:00", "url": outages.ISU_PAGE}
    district_only = {"id": "isu-2", "kind": "su", "title": "Gebze Hacıhalil'de arıza", "url": outages.ISU_PAGE}
    other_city = {"id": "isu-3", "kind": "su", "title": "İzmit Yahyakaptan su kesintisi", "url": outages.ISU_PAGE}
    assert planned.relevant_su(mine) and planned.relevant_su(district_only) and not planned.relevant_su(other_city)
    assert planned.affects_me(mine) and not planned.affects_me(district_only)
    text = planned.alert(mine, TODAY)
    assert text.startswith("💧 Su kesintisi duyurusu") and "Örnek Köy" in text and "Kaynak: https://www.isu.gov.tr" in text


def test_fetch_all_keeps_only_relevant_water_cuts(monkeypatch):
    async def fake_sedas(c): return []

    async def fake_isu(c):
        return [{"id": "isu-2", "kind": "su", "title": "Gebze Hacıhalil'de arıza", "url": outages.ISU_PAGE},
                {"id": "isu-3", "kind": "su", "title": "İzmit Yahyakaptan su kesintisi", "url": outages.ISU_PAGE}]
    monkeypatch.setattr(outages, "sedas", fake_sedas)
    monkeypatch.setattr(outages, "isu", fake_isu)
    items = run(planned.fetch_all())
    assert [o["id"] for o in items] == ["isu-2"]
    assert "Hacıhalil" in planned.section(items) and "Yahyakaptan" not in planned.section(items)


def test_one_source_failing_does_not_break_the_other(monkeypatch):
    async def broken(c): raise TimeoutError

    async def fake_sedas(c):
        return [el(1, TODAY, "ÖRNEK KÖY")]
    monkeypatch.setattr(outages, "sedas", fake_sedas)
    monkeypatch.setattr(outages, "isu", broken)
    assert [o["id"] for o in run(planned.fetch_all())] == ["sedas-1"]
