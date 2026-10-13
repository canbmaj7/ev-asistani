"""Planlı kesintiler: SEDAŞ (elektrik) ve İSU (su)."""
import hashlib
import html
import re
from datetime import datetime

import httpx

from .. import config
from ..util import norm

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


def _text(fragment: str) -> str:
    """HTML parçasını düz metne çevirir (etiketler atılır, boşluklar sadeleşir)."""
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", fragment, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()


def parse_isu(page: str) -> list[dict]:
    """İSU ana sayfasındaki 'SU KESİNTİLERİ' kaydırıcısı (#interruption-slider): her <li> bir kesinti.

    İçerik serbest metin (editörden gelen <font> etiketleri), bu yüzden alanlara ayrılmaz; tam metin saklanır
    ve mahalle metinde aranır. Sayfa yapısı tanınmazsa sessizce "kesinti yok" denmez, hata verilir.
    """
    start = page.find('id="interruption-slider"')
    empty = re.search(r'id="NoInterruptionPanel"[^>]*>(.*?)</div>', page, re.S)
    if start < 0 and not empty:
        raise ValueError("İSU sayfa yapısı değişmiş (kesinti bölümü bulunamadı)")
    out = []
    if start >= 0:
        end = page.find("</ul>", start)
        for li in re.findall(r"<li\b[^>]*>(.*?)</li>", page[start:end if end > 0 else None], re.S):
            text = _text(li)
            if not text:
                continue
            href = re.search(r'href="([^"#][^"]*)"', li)
            link = href.group(1) if href else ""
            url = link if link.startswith("http") else (ISU_PAGE.rstrip("/") + "/" + link.lstrip("/")) if link else ISU_PAGE
            out.append({"id": "isu-" + hashlib.sha1(norm(text).encode()).hexdigest()[:12], "kind": "su",
                        "title": text, "url": url})
    if not out and empty and "bulunmamaktadır" not in _text(empty.group(1)):
        raise ValueError("İSU kesinti bölümü okunamadı (beklenen 'kesinti yok' metni yok)")
    return out


async def isu(c: httpx.AsyncClient) -> list[dict]:
    r = await c.get(ISU_PAGE, follow_redirects=True)
    r.raise_for_status()
    return parse_isu(r.text)


async def isu_detail_text(c: httpx.AsyncClient, url: str) -> str:
    r = await c.get(url, follow_redirects=True)
    r.raise_for_status()
    return _text(r.text)
