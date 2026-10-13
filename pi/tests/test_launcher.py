"""Başlatıcı: imza doğrulama, sürüm/geri alma koruması ve yedeğe düşme (gerçek openssl ile)."""
import base64
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl yok")
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("launcher", ROOT / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def keypair(tmp: Path, name: str):
    priv, pub = tmp / f"{name}.pem", tmp / f"{name}.pub"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(priv)], check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(priv), "-pubout", "-out", str(pub)], check=True, capture_output=True)
    return priv, pub


def sign(priv: Path, data: bytes, tmp: Path) -> bytes:
    f = tmp / "to-sign"
    f.write_bytes(data)
    raw = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(priv), "-rawin", "-in", str(f)],
                         check=True, capture_output=True).stdout
    return base64.b64encode(raw)


def agent(version: int, body: str = "print('merhaba')") -> bytes:
    return f"AGENT_VERSION = {version}            # açıklama\n{body}\n".encode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    priv, pub = keypair(tmp_path, "release")
    baked = tmp_path / "baked.py"
    baked.write_bytes(agent(5, "print('yedek')"))
    run_dir = tmp_path / "run"
    served = {}                                                    # url_son_eki → bayt

    def fake_fetch_release(name, api=None, base=None):
        for suffix, data in served.items():
            if name == suffix:
                if isinstance(data, Exception):
                    raise data
                return data
        raise OSError("404")
    monkeypatch.setattr(launcher, "fetch_release", fake_fetch_release)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)

    class E:
        pass
    e = E()
    e.priv, e.pub, e.baked, e.run_dir, e.served, e.tmp = priv, pub, baked, run_dir, served, tmp_path
    e.publish = lambda data, key=priv: served.update({"heartbeat.py": data, "heartbeat.py.sig": sign(key, data, tmp_path)})
    e.choose = lambda: launcher.choose("http://x", str(pub), str(baked), str(run_dir), api=None)
    return e


def test_valid_newer_signed_agent_is_used(env):
    env.publish(agent(6, "print('yeni')"))
    path = env.choose()
    assert path == str(env.run_dir / "heartbeat.py") and b"yeni" in Path(path).read_bytes()
    assert oct((env.run_dir / "heartbeat.py").stat().st_mode & 0o777) == "0o600"          # yalnızca root okur/yazar


def test_tampered_file_is_rejected(env):
    good = agent(6, "print('yeni')")
    env.publish(good)
    env.served["heartbeat.py"] = good.replace(b"yeni", b"KOTU!")                           # imza eski içeriğe ait
    assert env.choose() == str(env.baked)


def test_signature_from_another_key_is_rejected(env):
    other_priv, _ = keypair(env.tmp, "saldirgan")
    env.publish(agent(9, "print('saldırgan')"), key=other_priv)
    assert env.choose() == str(env.baked)


def test_garbage_or_missing_signature_is_rejected(env):
    env.publish(agent(6))
    env.served["heartbeat.py.sig"] = b"bu-base64-degil!!"
    assert env.choose() == str(env.baked)
    env.served["heartbeat.py.sig"] = base64.b64encode(b"x" * 64)                          # geçerli biçim, yanlış imza
    assert env.choose() == str(env.baked)
    del env.served["heartbeat.py.sig"]
    assert env.choose() == str(env.baked)


def test_downgrade_is_rejected_even_if_signed(env):
    env.publish(agent(4, "print('eski ama imzalı')"))                                     # yedeğin (5) altında
    assert env.choose() == str(env.baked)
    env.publish(agent(7, "print('v7')"))
    env.choose()
    env.publish(agent(6, "print('v6')"))                                                  # çalışan 7'den eski
    assert env.choose() == str(env.run_dir / "heartbeat.py")
    assert b"v7" in (env.run_dir / "heartbeat.py").read_bytes()


def test_offline_falls_back_to_cached_then_baked(env):
    assert env.choose() == str(env.baked)                                                 # hiç indirilmemiş, internet yok
    env.publish(agent(6, "print('v6')"))
    env.choose()
    env.served.clear()                                                                    # internet gitti
    assert env.choose() == str(env.run_dir / "heartbeat.py")                              # önceki doğrulanmış sürüm


def test_missing_version_and_oversize_are_rejected(env):
    env.publish("print('sürüm numarası yok')\n".encode())
    assert env.choose() == str(env.baked)
    env.served["heartbeat.py"] = ValueError("dosya çok büyük")
    assert env.choose() == str(env.baked)


def test_version_regex_accepts_trailing_comment():
    assert launcher.version_of(b"x = 1\nAGENT_VERSION = 12            # not\n") == 12
    assert launcher.version_of(b"AGENT_VERSION = 3\n") == 3
    assert launcher.version_of(b"# AGENT_VERSION = 3\nAGENT_VERSION_X = 4\n") is None


def test_repo_agent_signature_matches_public_key():
    """Repodaki pi/heartbeat.py, repodaki genel anahtarla doğrulanan imzaya sahip olmalı (unutulan imzayı yakalar)."""
    sig = (ROOT / "heartbeat.py.sig").read_bytes()
    assert launcher.verify((ROOT / "heartbeat.py").read_bytes(), sig, str(ROOT / "release.pub")), \
        "pi/heartbeat.py değişti ama imzalanmadı: scripts/release-agent.sh çalıştır"
    assert launcher.version_of((ROOT / "heartbeat.py").read_bytes()) is not None


def test_fetch_release_prefers_api_then_falls_back_to_raw(monkeypatch):
    calls = []

    def fake_fetch(url, timeout=20, accept=None):
        calls.append((url, accept))
        if "api.github.com" in url and len(calls) == 1:
            raise OSError("API sınırı aşıldı")
        return b"veri"
    monkeypatch.setattr(launcher, "fetch", fake_fetch)
    assert launcher.fetch_release("heartbeat.py", "https://api.github.com/x/contents/pi", "https://raw.example/pi") == b"veri"
    assert calls[0] == ("https://api.github.com/x/contents/pi/heartbeat.py?ref=main", "application/vnd.github.raw+json")
    assert calls[1] == ("https://raw.example/pi/heartbeat.py", None)                         # API düştü → raw
    calls.clear()
    monkeypatch.setattr(launcher, "fetch", lambda url, timeout=20, accept=None: (_ for _ in ()).throw(OSError("yok")))
    with pytest.raises(OSError):
        launcher.fetch_release("heartbeat.py", "https://api.github.com/x/contents/pi", "https://raw.example/pi")
