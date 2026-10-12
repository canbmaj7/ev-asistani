"""Nöbetçi eczaneler: Habertürk'ün ilçe listesi."""
import html
import re

import httpx

from .. import config


def parse(page: str) -> tuple[str, list[dict]]:
    """(geçerli tarih metni, [{name, address, phone}]) döndürür."""
    m = re.search(r"(\d{1,2} (?:Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık) \d{4} \w+)", page)
    items = []
    for block in re.findall(r'<li class="overflow-hidden w-full.*?</li>', page, re.S):
        name = re.search(r'<p class="block w-full font-bold">\s*(.*?)\s*</p>', block, re.S)
        addr = re.search(r'Adres:\s*</span>\s*(.*?)\s*</p>', block, re.S)
        phone = re.search(r'Telefon:\s*</span>\s*(.*?)\s*</p>', block, re.S)
        if name and "Eczane" in name.group(1):
            items.append({k: html.unescape(re.sub(r"\s+", " ", v.group(1))).strip() if v else ""
                          for k, v in (("name", name), ("address", addr), ("phone", phone))})
    if not items:
        raise ValueError("eczane listesi okunamadı (sayfa yapısı değişmiş olabilir)")
    return (m.group(1) if m else ""), items


async def fetch(c: httpx.AsyncClient) -> tuple[str, list[dict]]:
    r = await c.get(config.PHARMACY_URL, follow_redirects=True)
    r.raise_for_status()
    return parse(r.text)
