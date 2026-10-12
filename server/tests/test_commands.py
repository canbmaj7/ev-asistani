"""Komutlar, üyelik ve yetkiler."""
from conftest import MEMBER, OWNER, STRANGER, run

from asistan import commands, home, invites, members


def say(chat_id, text, name="Test", message_id=1):
    head, _, arg = text.partition(" ")
    run(commands.handle(chat_id, {"from": {"first_name": name}, "message_id": message_id}, head, arg))


def test_stranger_blocked(outbox):
    say(STRANGER, "/durum")
    assert "özel" in outbox.last()


def test_invite_flow(outbox):
    say(STRANGER, "/katil YANLIS")
    assert "geçersiz" in outbox.last()
    say(OWNER, "/davet")
    code = next(iter(invites.codes))
    assert f"/katil {code}" in outbox.last()
    say(MEMBER, f"/katil {code}", name="Kardeş")
    assert members.is_member(MEMBER)
    assert any("Katıldın" in t for t in outbox.texts(MEMBER))
    assert "Kardeş bildirimlere katıldı" in outbox.texts(OWNER)[-1]
    say(STRANGER, f"/katil {code}")            # tek kullanımlık
    assert not members.is_member(STRANGER)


def test_member_cannot_use_owner_commands(outbox, member):
    for cmd in ("/davet", "/esik 3", "/cihazlar", "/uyeler"):
        outbox.clear()
        say(member, cmd)
        assert not outbox, cmd
    assert not invites.codes and home.timeout_sec() == 120


def test_leave(outbox, member):
    say(member, "/ayril", name="Kardeş")
    assert not members.is_member(member)
    assert "ayrıldı" in outbox.texts(OWNER)[-1]


def test_owner_removes_member(outbox, member):
    say(OWNER, "/uyeler")
    assert f"ID {member}" in outbox.last()
    say(OWNER, f"/cikar {member}")
    assert "çıkarıldı" in outbox.last() and not members.is_member(member)


def test_brute_force_lockout(outbox):
    for i in range(10):
        say(STRANGER, f"/katil K{i}")
    outbox.clear()
    say(STRANGER, "/katil AAAAAA")
    assert not outbox                          # susar


def test_threshold(outbox):
    say(OWNER, "/esik")
    assert "2 dk" in outbox.last()
    say(OWNER, "/esik 5")
    assert home.timeout_sec() == 300
    say(OWNER, "/esik 1")
    assert "arasında" in outbox.last() and home.timeout_sec() == 300
    say(OWNER, "/esik abc")
    assert "Kullanım" in outbox.last()


def test_report_and_history(outbox):
    say(OWNER, "/rapor")
    assert "Son 7 gün" in outbox.last() and "Son 30 gün" in outbox.last() and "Erişilebilirlik" in outbox.last()
    say(OWNER, "/rapor 3")
    assert "Son 3 gün" in outbox.last()
    say(OWNER, "/gecmis")
    assert "Kayıtlı kesinti yok" in outbox.last()


def test_menus():
    owner = {c["command"] for c in commands.OWNER_MENU}
    member = {c["command"] for c in commands.MEMBER_MENU}
    assert {"davet", "esik", "cihazlar"} <= owner and not {"davet", "esik"} & member
    assert {"durum", "ozet", "deprem", "takvim"} <= owner & member
