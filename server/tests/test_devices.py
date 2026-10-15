"""Ağ bekçisi."""
import time

from conftest import OWNER, run

from asistan import devices


def scan(*macs, net=""):
    run(devices.handle_scan([{"mac": m, "ip": f"192.168.1.{10 + i}", "host": ""} for i, m in enumerate(macs)], time.time(), net=net))


def test_baseline_then_new_device(outbox):
    scan("aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02")
    assert "2 cihaz" in outbox.last() and "başlangıç" in outbox.last()
    outbox.clear()
    scan("aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02")
    assert not outbox
    scan("aa:bb:cc:00:00:01", "de:ad:be:ef:00:09")
    assert len(outbox) == 1 and outbox[0][0] == (OWNER,) and "yeni bir cihaz" in outbox[0][1]
    outbox.clear()
    scan("de:ad:be:ef:00:09")
    assert not outbox                          # aynı cihaz bir kez bildirilir


def test_invalid_macs_ignored(outbox):
    run(devices.handle_scan([{"mac": "<script>"}, {"mac": "zz"}, {"mac": "00:00:00:00:00:00"}], time.time()))
    assert not outbox and devices.listing() == []


def test_rename(outbox):
    scan("aa:bb:cc:00:00:01", "ff:ff:ff:00:00:01", "de:ad:be:ef:00:09")
    assert "Salon TV" in devices.rename("0009 Salon TV")
    assert "Birden çok" in devices.rename("0001 x")
    assert "yok" in devices.rename("9999 x")
    assert "Kullanım" in devices.rename("0009")
    assert "Salon TV" in devices.text() and "3 çevrimiçi" in devices.text()


def test_network_change_starts_a_silent_new_baseline(outbox):
    """Pi başka Wi-Fi'ye taşınınca yeni ağdaki cihazlar tek tek 'yeni cihaz' diye bildirilmez."""
    scan("aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02", net="TP-LINK")
    outbox.clear()
    scan("aa:bb:cc:00:00:01", "de:ad:be:ef:00:10", "de:ad:be:ef:00:11", net="Keenetic")
    assert len(outbox) == 1 and "(Keenetic) 3 cihaz" in outbox.last() and "başlangıç" in outbox.last()
    outbox.clear()
    scan("aa:bb:cc:00:00:01", "de:ad:be:ef:00:10", "de:ad:be:ef:00:11", net="Keenetic")
    assert not outbox
    scan("aa:bb:cc:00:00:01", "de:ad:be:ef:00:10", "ff:00:00:00:00:99", net="Keenetic")
    assert len(outbox) == 1 and "yeni bir cihaz" in outbox.last()           # yeni ağda gerçekten yeni olan hâlâ bildirilir


def test_migration_adds_net_column(tmp_path, monkeypatch):
    import sqlite3
    from asistan import config, db
    path = str(tmp_path / "eski.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE devices (mac TEXT PRIMARY KEY, ip TEXT, host TEXT, name TEXT NOT NULL DEFAULT '', "
                "first_seen REAL NOT NULL, last_seen REAL NOT NULL)")
    con.execute("INSERT INTO devices VALUES ('aa:bb:cc:00:00:01', '1.2.3.4', '', 'TV', 1, 1)")
    con.commit()
    con.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init_db()
    with db.db() as c:
        row = c.execute("SELECT name, net FROM devices").fetchone()
    assert (row["name"], row["net"]) == ("TV", "")                          # eski veri korunur


def test_router_listed_devices_join_silently_once(outbox):
    scan("aa:bb:cc:00:00:01")
    outbox.clear()
    run(devices.handle_scan([{"mac": "aa:bb:cc:00:00:01", "ip": "1.1.1.1", "host": ""},
                             {"mac": "7c:21:4a:00:00:04", "ip": "1.1.1.2", "host": "Can", "via": "router"}], time.time()))
    assert not outbox                                           # ilk modem listesi: bildirim yok
    assert any(d["name"] or d["host"] == "Can" for d in devices.listing())
    run(devices.handle_scan([{"mac": "aa:bb:cc:00:00:01", "ip": "1.1.1.1", "host": ""},
                             {"mac": "7c:21:4a:00:00:09", "ip": "1.1.1.3", "host": "Yeni", "via": "router"}], time.time()))
    assert len(outbox) == 1 and "yeni bir cihaz" in outbox[0][1]  # sonraki yeni cihaz bildirilir


def test_old_devices_hidden_in_text(outbox):
    from asistan.db import db
    scan("aa:bb:cc:00:00:01")
    with db() as con:
        con.execute("INSERT INTO devices (mac, ip, host, first_seen, last_seen, net) VALUES ('de:ad:00:00:00:01', '', '', 1, 1, '')")
    text = devices.text()
    assert "1 eski kayıt gizlendi" in text and "1 çevrimiçi / 1 kayıtlı" in text
