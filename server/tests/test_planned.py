"""Planlı kesintiler: mahalle eşleştirme, uyarılar ve İSU ayrıştırıcı."""
import time
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


def sig(o):
    return {**o, "sig": f"{o['day'].isoformat()} {o['start']}-{o['end']}"}


def test_notify_only_my_neighbourhood_and_only_once(outbox):
    items = [sig(el(1, TODAY + timedelta(days=1), "ÖRNEK KÖY, BALÇIK")), sig(el(2, TODAY + timedelta(days=1), "YENİKENT")),
             sig(el(3, TODAY - timedelta(days=1), "ÖRNEK KÖY"))]          # geçmiş: bildirilmez
    run(planned.notify(items))
    run(planned.notify(items))                                            # ikinci okuma: tekrar yok
    assert len(outbox) == 1 and outbox[0][0] == ("HERKES",)
    text = outbox.last()
    assert "Planlı elektrik kesintisi" in text and "Yarın 09:00–17:00" in text and "🔗 Kaynak: https://www.sedas.com" in text
    assert "Güncellendi" not in text
    assert len(kv_json("planned_seen", [])) == 3


def test_changed_time_alerts_again(outbox):
    day = TODAY + timedelta(days=1)
    run(planned.notify([sig(el(1, day, "ÖRNEK KÖY", "09:00", "17:00"))]))
    run(planned.notify([sig(el(1, day, "ÖRNEK KÖY", "10:00", "14:00"))]))   # SEDAŞ saati değiştirdi
    assert len(outbox) == 2 and outbox[1][1].startswith("🔄 Güncellendi") and "10:00–14:00" in outbox[1][1]


def test_finished_outage_today_not_announced(outbox):
    now = datetime.now(config.TZ)
    if now.hour == 0 and now.minute < 5:
        pytest.skip("gece yarısı sınırı")
    past_end = f"{now.hour:02d}:{max(now.minute - 1, 0):02d}" if now.minute else "00:00"
    run(planned.notify([sig(el(1, TODAY, "ÖRNEK KÖY", "00:00", past_end))]))
    assert not outbox
    run(planned.notify([sig(el(2, TODAY, "ÖRNEK KÖY", "00:00", "23:59"))]))  # henüz bitmedi
    assert len(outbox) == 1


def test_water_cut_text_match_alerts_immediately(outbox):
    su = {"id": "isu-1", "kind": "su", "title": "Gebze Örnek Köy Mahallesi'nde su kesintisi", "url": outages.ISU_PAGE}
    run(planned.notify([su]))
    assert outbox.last().startswith("💧 Su kesintisi duyurusu")


def _sources(monkeypatch, sedas=None, isu=None):
    calls = {"sedas": 0, "isu": 0}

    async def fs():
        calls["sedas"] += 1
        if isinstance(sedas, Exception):
            raise sedas
        return sedas or []

    async def fi():
        calls["isu"] += 1
        if isinstance(isu, Exception):
            raise isu
        return isu or []
    monkeypatch.setattr(planned, "fetch_sedas", fs)
    monkeypatch.setattr(planned, "fetch_isu", fi)
    return calls


def test_poll_respects_interval_and_sources_are_independent(monkeypatch):
    calls = _sources(monkeypatch, sedas=[sig(el(1, TODAY, "YENİKENT"))])
    state = {"next": 0.0, "fails": 0}
    t0 = time.time()
    run(planned.poll("sedas", planned.fetch_sedas, 1800, state))
    run(planned.poll("sedas", planned.fetch_sedas, 1800, state))        # aralık dolmadı → istek yok
    assert calls["sedas"] == 1 and calls["isu"] == 0
    assert 1800 * 0.9 - 5 <= state["next"] - t0 <= 1800 * 1.1 + 5
    state["next"] = 0
    run(planned.poll("sedas", planned.fetch_sedas, 1800, state))
    assert calls["sedas"] == 2


def test_poll_backs_off_on_errors_and_keeps_other_cache(monkeypatch):
    _sources(monkeypatch, sedas=[sig(el(1, TODAY, "YENİKENT"))], isu=TimeoutError())
    ok, bad = {"next": 0.0, "fails": 0}, {"next": 0.0, "fails": 0}
    run(planned.poll("sedas", planned.fetch_sedas, 1800, ok))
    for expected in (1200, 2400, 3600, 3600):                           # 600·2ⁿ, en çok 1 saat
        bad["next"] = 0
        t = time.time()
        run(planned.poll("isu", planned.fetch_isu, 600, bad))
        assert abs((bad["next"] - t) - expected) < 5
    assert bad["fails"] == 4
    assert [o["id"] for o in planned.cached()] == ["sedas-1"]          # İSU hatası SEDAŞ önbelleğini silmedi


def test_only_sedas_and_isu_are_contacted():
    """Kesinti verisi yalnızca SEDAŞ ve İSU'dan: kaynak modülündeki tüm adresler bu iki alan adında."""
    import re
    from pathlib import Path
    code = Path(outages.__file__).read_text(encoding="utf-8")
    hosts = set(re.findall(r"https?://([a-z0-9.-]+)", code))
    assert hosts and all(h.endswith(("sedas.com", "isu.gov.tr")) for h in hosts), hosts
    planned_code = Path(planned.__file__).read_text(encoding="utf-8")
    assert not re.findall(r"https?://", planned_code)                   # planned.py kendi başına siteye gitmez


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
