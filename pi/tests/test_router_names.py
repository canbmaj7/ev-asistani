"""Modem panelindeki cihaz adlarının temizlenmesi."""
import importlib.util
import os
from pathlib import Path

os.environ.update(NOBETCI_URL="http://x/beat", NOBETCI_TOKEN="t")
spec = importlib.util.spec_from_file_location("heartbeat", Path(__file__).resolve().parents[1] / "heartbeat.py")
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)


def test_auto_suffix_removed():
    assert hb.clean_router_name("MSI - Birincil - 2026-10-13 13:43", "MSI") == "MSI"


def test_custom_name_kept_and_hostname_fallback():
    assert hb.clean_router_name("Babamın telefonu", "iPhone") == "Babamın telefonu"
    assert hb.clean_router_name("", "iPhone") == "iPhone"


def test_no_router_settings_means_no_hosts():
    assert hb.router_hosts() == []


def test_merge_names_modem_repeater_and_dedupe():
    arp = [{"ip": "192.168.1.1", "mac": "50:ff:20:00:00:01", "host": ""},
           {"ip": "192.168.1.49", "mac": "8a:c3:97:00:00:02", "host": ""},
           {"ip": "192.168.1.123", "mac": "8a:c3:97:00:00:02", "host": ""},     # repeater proxy-ARP
           {"ip": "192.168.1.89", "mac": "34:5a:60:00:00:03", "host": "x"}]
    hosts = [{"mac": "34:5a:60:00:00:03", "ip": "192.168.1.89", "active": True, "name": "MSI"},
             {"mac": "7c:21:4a:00:00:04", "ip": "192.168.1.123", "active": True, "name": "Can"},
             {"mac": "24:eb:16:00:00:05", "ip": "0.0.0.0", "active": False, "name": "Eski"}]
    out = {d["mac"]: d for d in hb.merge_router(arp, hosts, "192.168.1.1")}
    assert out["50:ff:20:00:00:01"]["host"] == "Modem"
    assert out["34:5a:60:00:00:03"]["host"] == "MSI"
    assert out["7c:21:4a:00:00:04"] == {"ip": "192.168.1.123", "mac": "7c:21:4a:00:00:04", "host": "Can", "via": "router"}
    assert "24:eb:16:00:00:05" not in out and len(out) == 4
