"""Planlı kesintiler: SEDAŞ (elektrik) ve İSU (su)."""
import html
import re
from datetime import datetime

import httpx

from .. import config

SEDAS_PAGE = "https://www.sedas.com/Tr/SupplyContinuityUrl/PlannedView"
SEDAS_API = "https://www.sedas.com/Home/PostPlannedListAll"
SEDAS_OLD_API = "https://www.sedas.com/Home/PostPlannedOldListAll"
ISU_PAGE = "https://www.isu.gov.tr/"


def _parse_sedas(rows: list[dict], district: str) -> list[dict]:
    out = []
    for x in rows:
        if (x.get("ZILCE") or "").upper() != district:
            continue
        try:
            day = datetime.strptime(x["ZPLANTAR"], "%d.%m.%Y").date()
        except (KeyError, ValueError):
            continue
        out.append({
            "id": f"sedas-{x.get('ZPKNO') or x.get('Id')}",
            "kind": "elektrik", "day": day,
            "start": x.get("ZBASSAAT") or "?", "end": x.get("ZBITSAAT") or "?",
            "areas": (x.get("ZMAHALLE") or "").strip(" ,"),
            "streets": (x.get("ZCSBM") or "").strip(" ,"),
            "url": SEDAS_PAGE,
        })
    return sorted(out, key=lambda o: (o["day"], o["start"]))


async def sedas(c: httpx.AsyncClient, district: str | None = None) -> list[dict]:
    """İlçedeki güncel planlı elektrik kesintileri."""
    district = (district or config.DISTRICT).upper()
    r = await c.post(SEDAS_API, data={"page": 1, "pageSize": 500, "City": "KOCAELİ", "District": district},
                     headers={"X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    return _parse_sedas(r.json().get("Data", []), district)


async def sedas_history(c: httpx.AsyncClient, start: str, end: str, district: str | None = None) -> list[dict]:
    """Geçmiş planlı kesintiler (SEDAŞ 'Geçmiş Kesintiler' sayfası); start/end 'YYYY-MM-DD'. Test ve analiz için."""
    district = (district or config.DISTRICT).upper()
    rows, page = [], 1
    while True:
        r = await c.post(SEDAS_OLD_API, data={"page": page, "pageSize": 500, "City": "KOCAELİ", "District": district,
                                              "StartDate": start, "EndDate": end},
                         headers={"X-Requested-With": "XMLHttpRequest"})
        r.raise_for_status()
        body = r.json()
        rows += body.get("Data", [])
        if not body.get("Data") or len(rows) >= body.get("Total", 0):
            return _parse_sedas(rows, district)
        page += 1


def parse_isu(page: str) -> list[dict]:
    """İSU ana sayfasındaki 'SU KESİNTİLERİ' kartı: her kesinti bir detay bağlantısı."""
    s = html.unescape(page)
    i = s.find("SU KESİNTİLERİ")
    if i < 0:
        raise ValueError("İSU sayfa yapısı değişmiş")
    card = s[i:i + 20000]
    card = card[:card.find('uk-card-title', 50)] if card.find('uk-card-title', 50) > 0 else card
    out = []
    for href, title in re.findall(r'href="([^"]*sukesintileri/detay\.aspx\?Id=(?:\d+))"[^>]*>(.*?)</a>', card, re.S):
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", title)).strip()
        out.append({"id": "isu-" + href.split("Id=")[-1], "kind": "su", "title": title,
                    "url": href if href.startswith("http") else "https://www.isu.gov.tr" + ("" if href.startswith("/") else "/") + href})
    if not out and "bulunmamaktadır" not in card:
        raise ValueError("İSU kesinti kartı okunamadı (sayfa yapısı değişmiş olabilir)")
    return out


async def isu(c: httpx.AsyncClient) -> list[dict]:
    r = await c.get(ISU_PAGE, follow_redirects=True)
    r.raise_for_status()
    return parse_isu(r.text)


async def isu_detail_text(c: httpx.AsyncClient, url: str) -> str:
    r = await c.get(url, follow_redirects=True)
    r.raise_for_status()
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()
