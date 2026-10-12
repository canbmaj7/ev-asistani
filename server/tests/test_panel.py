"""Web paneli: kimlik doğrulama ve veri."""
import base64

import pytest
from conftest import run

from asistan import config


def auth(pw):
    return {"Authorization": "Basic " + base64.b64encode(f"x:{pw}".encode()).decode()}


@pytest.fixture
def password(monkeypatch):
    monkeypatch.setattr(config, "PANEL_PASSWORD", "gizli123")


def test_auth(client, password):
    async def go():
        async with client as c:
            assert (await c.get("/panel")).status_code == 401
            assert (await c.get("/panel", headers=auth("yanlis"))).status_code == 401
            r = await c.get("/panel", headers=auth("gizli123"))
            assert r.status_code == 200 and "Ev Asistanı" in r.text and "Content-Security-Policy" in r.text
            for path in ("/panel/data",):
                assert (await c.get(path)).status_code == 401
            assert (await c.post("/panel/live")).status_code == 401
    run(go())


def test_data(client, password):
    async def go():
        async with client as c:
            return (await c.get("/panel/data", headers=auth("gizli123"))).json()
    d = run(go())
    assert {"status", "stats", "daily", "outages", "devices", "info", "timeout_sec"} <= set(d)
    assert len(d["daily"]) == 30


def test_lockout_after_failures(client, password):
    async def go():
        async with client as c:
            for _ in range(11):
                await c.get("/panel", headers=auth("yanlis"))
            return (await c.get("/panel", headers=auth("gizli123"))).status_code
    assert run(go()) == 429


def test_disabled_without_password(client, monkeypatch):
    monkeypatch.setattr(config, "PANEL_PASSWORD", "")

    async def go():
        async with client as c:
            return (await c.get("/panel", headers=auth("x"))).status_code
    assert run(go()) == 404
