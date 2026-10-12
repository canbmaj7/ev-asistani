"""Kişisel takvim: akış, gizlilik ve iç ağ koruması."""
import pytest
from conftest import MEMBER, OWNER, STRANGER, run

from asistan import calendars, commands
from asistan.sources import calendar as cal_src


@pytest.mark.parametrize("url", ["http://example.com/a.ics", "https://127.0.0.1/a.ics",
                                 "https://10.0.1.6:8000/x", "https://localhost/a.ics", "https://coolify/a.ics"])
def test_private_addresses_rejected(url):
    assert not cal_src.is_public_https(url)


@pytest.fixture
def fake_calendar(monkeypatch):
    async def section(c, url, day):
        if "bozuk" in url:
            raise ValueError("okunamadı")
        return "📅 Bugün\n09:00 · Toplantı"
    monkeypatch.setattr(cal_src, "section", section)
    monkeypatch.setattr(cal_src, "is_public_https", lambda url: url.startswith("https://") and "127.0.0.1" not in url)


def say(chat_id, text, message_id=1):
    head, _, arg = text.partition(" ")
    run(commands.handle(chat_id, {"from": {"first_name": "T"}, "message_id": message_id}, head, arg))


def test_flow(outbox, member, fake_calendar):
    say(member, "/takvim")
    assert "iCal" in outbox.last() and calendars.expects_link(member, "https://x/a.ics")
    run(calendars.save(member, "https://takvim.example/a.ics", 55))
    assert (member, 55) in outbox.deleted                      # gizli link mesajı silindi
    assert "bağlandı" in outbox.last() and "Toplantı" in outbox.last()
    assert calendars.get(member) == "https://takvim.example/a.ics" and calendars.get(OWNER) == ""
    say(member, "/takvim sil")
    assert "kaldırıldı" in outbox.last() and calendars.get(member) == ""


def test_bad_links_not_saved(outbox, fake_calendar):
    say(OWNER, "/takvim https://127.0.0.1/x.ics")
    assert "olmadı" in outbox.last()
    say(OWNER, "/takvim https://bozuk.example/x.ics")
    assert "okuyamadım" in outbox.last()
    assert calendars.get(OWNER) == ""


def test_webcal_converted(outbox, fake_calendar):
    run(calendars.save(OWNER, "webcal://takvim.example/a.ics", 1))
    assert calendars.get(OWNER) == "https://takvim.example/a.ics"


def test_stranger_cannot(outbox, fake_calendar):
    say(STRANGER, "/takvim https://takvim.example/a.ics")
    assert calendars.get(STRANGER) == ""
