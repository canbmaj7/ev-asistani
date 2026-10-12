"""Ağ bekçisi."""
import time

from conftest import OWNER, run

from asistan import devices


def scan(*macs):
    run(devices.handle_scan([{"mac": m, "ip": f"192.168.1.{10 + i}", "host": ""} for i, m in enumerate(macs)], time.time()))


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
