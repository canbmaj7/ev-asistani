import asyncio
import os
import sys
from pathlib import Path

os.environ.update(TELEGRAM_TOKEN="test", TELEGRAM_CHAT_ID="100", BEAT_TOKEN="beat-token",
                  DB_PATH="/tmp/asistan-test-unused.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402

from asistan import calendars, config, db, home, invites, members, panel  # noqa: E402
from asistan import telegram as tg  # noqa: E402

OWNER, MEMBER, STRANGER = 100, 200, 300


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    """Her test boş bir veritabanı ve sıfır durumla başlar."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    home.state.update(last_beat=0.0, boot_id=None, device=None, down_since=None, info={})
    home.live_waiters.clear()
    home.ping_event.clear()
    invites.codes.clear()
    invites.bad_attempts.clear()
    calendars.awaiting.clear()
    panel.failures.clear()


class Outbox(list):
    """Gönderilen Telegram mesajları: [(alıcılar, metin)]; alıcı None ise 'HERKES'."""
    deleted: list

    def texts(self, chat_id=None):
        return [t for to, t in self if chat_id is None or chat_id in to]

    def last(self):
        return self[-1][1] if self else ""


@pytest.fixture
def outbox(monkeypatch):
    box = Outbox()
    box.deleted = []

    async def fake_send(text, to=None):
        box.append((tuple(to) if to is not None else ("HERKES",), text))

    async def fake_delete(chat_id, message_id):
        box.deleted.append((chat_id, message_id))
        return True

    monkeypatch.setattr(tg, "send", fake_send)
    monkeypatch.setattr(tg, "delete_message", fake_delete)
    return box


@pytest.fixture
def member():
    members.add(MEMBER, "Kardeş")
    return MEMBER


@pytest.fixture
def client():
    """Uygulamaya HTTP istemcisi (arka plan döngüleri başlatılmaz)."""
    from asistan.main import app
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def run(coro):
    return asyncio.run(coro)
