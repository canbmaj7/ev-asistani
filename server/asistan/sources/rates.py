"""Döviz kuru: TCMB today.xml (döviz satış)."""
import xml.etree.ElementTree as ET

import httpx

from .common import tr_num

CODES = ("USD", "EUR", "GBP")


async def section(c: httpx.AsyncClient, state: dict) -> str:
    """`state` bir önceki iş gününün kurlarını tutar (değişim yüzdesi için); yerinde güncellenir."""
    r = await c.get("https://www.tcmb.gov.tr/kurlar/today.xml")
    r.raise_for_status()
    root = ET.fromstring(r.content)
    tarih = root.get("Tarih", "")
    now = {cur.get("Kod"): float(cur.findtext("ForexSelling") or 0)
           for cur in root.findall("Currency") if cur.get("Kod") in CODES}
    hist = state.setdefault("rates", {})
    if hist.get("tarih") != tarih:                # yeni iş günü: dünkü değerleri sakla
        hist["prev"] = hist.get("values", {})
        hist["tarih"], hist["values"] = tarih, now
    prev = hist.get("prev", {})
    parts = []
    for kod, val in now.items():
        part = f"{kod} {tr_num(val)}"
        if prev.get(kod):
            ch = val - prev[kod]
            part += " ▲" if ch > 0 else " ▼" if ch < 0 else ""
        parts.append(part)
    return "💱 " + " · ".join(parts)
