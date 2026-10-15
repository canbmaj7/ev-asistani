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


def test_no_router_settings_means_no_names():
    assert hb.router_names() == {}
