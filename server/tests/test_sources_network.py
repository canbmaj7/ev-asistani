"""Gerçek dış servisler (internet gerekir): `pytest -m network`"""
from datetime import datetime

import pytest
from conftest import run

from asistan import config
from asistan.sources import calendar as cal_src
from asistan.sources import outages, pharmacy, prayer, quakes, rates, weather
from asistan.sources.common import client

pytestmark = pytest.mark.network
HOLIDAYS = "https://calendar.google.com/calendar/ical/tr.turkish%23holiday%40group.v.calendar.google.com/public/basic.ics"


def with_client(fn):
    async def go():
        async with client() as c:
            return await fn(c)
    return run(go())


def test_weather():
    assert "Hava · " in with_client(weather.section)


def test_rates():
    text = with_client(lambda c: rates.section(c, {}))
    assert "USD" in text and "EUR" in text


def test_afad():
    assert isinstance(with_client(lambda c: quakes.fetch(c, hours=240)), list)


def test_calendar_holidays():
    text = with_client(lambda c: cal_src.section(c, HOLIDAYS, datetime(2026, 10, 29, 9, tzinfo=config.TZ)))
    assert "Cumhuriyet Bayramı" in text


def test_redirect_to_private_blocked():
    with pytest.raises(ValueError):
        with_client(lambda c: cal_src.fetch_public(c, "https://httpbin.org/redirect-to?url=https://127.0.0.1/x.ics"))


def test_sedas():
    assert isinstance(with_client(outages.sedas), list)


def test_isu():
    assert isinstance(with_client(outages.isu), list)


def test_prayer():
    days = with_client(prayer.month)
    assert days and "Imsak" in days[0]


def test_pharmacy():
    day, items = with_client(pharmacy.fetch)
    assert items and "Eczane" in items[0]["name"]


def test_air_quality():
    assert "Hava kalitesi" in with_client(weather.air_quality)
