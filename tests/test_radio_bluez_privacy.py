"""Address privacy in main.conf: one line changed or added and every other byte left alone, the
commands that install it with a backup, the rule that puts the backup back, and the words."""

from __future__ import annotations

import pytest

from quackd.radio.bluez.privacy import (
    BACKUP,
    MAIN_CONF,
    PRIVATE,
    install_commands,
    privacy_setting,
    privacy_words,
    restore_commands,
    should_restore,
    with_privacy,
)

#: The shape of BlueZ's own main.conf: a commented default under [General], other groups after.
STOCK = """[General]

# Default adapter name
#Name = BlueZ

# Default privacy setting.
#Privacy = off

[Policy]
AutoEnable=true

[LE]
#MinAdvertisementInterval = 0
"""


def changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    return [(x, y) for x, y in zip(a, b, strict=False) if x != y]


def test_a_stock_file_gains_one_line_under_general_and_nothing_else_moves() -> None:
    edited = with_privacy(STOCK)
    assert privacy_setting(STOCK) is None and privacy_setting(edited) == "device"
    lines = edited.splitlines(keepends=True)
    assert lines[0] == "[General]\n" and lines[1] == "Privacy = device\n"
    assert "".join(lines[:1] + lines[2:]) == STOCK


def test_a_setting_already_there_is_replaced_where_it_stands() -> None:
    text = STOCK.replace("#Privacy = off", "Privacy = off")
    edited = with_privacy(text)
    assert changed_lines(text, edited) == [("Privacy = off\n", "Privacy = device\n")]
    assert len(edited.splitlines()) == len(text.splitlines())
    # GKeyFile takes the last of two, so the last is the one changed
    twice = "[General]\nPrivacy = off\nPrivacy=network\n"
    assert with_privacy(twice) == "[General]\nPrivacy = off\nPrivacy = device\n"


def test_a_privacy_line_in_another_group_is_not_the_general_one() -> None:
    text = "[General]\nName = robot\n[LE]\nPrivacy = off\n"
    assert privacy_setting(text) is None
    assert with_privacy(text) == "[General]\nPrivacy = device\nName = robot\n[LE]\nPrivacy = off\n"


def test_a_file_with_no_general_group_gets_one_at_the_top() -> None:
    assert with_privacy("") == "[General]\nPrivacy = device\n\n"
    assert with_privacy("[LE]\nX=1\n") == "[General]\nPrivacy = device\n\n[LE]\nX=1\n"


def test_crlf_line_endings_and_a_missing_final_newline_are_kept() -> None:
    crlf = STOCK.replace("\n", "\r\n")
    edited = with_privacy(crlf)
    assert edited.replace("Privacy = device\r\n", "", 1) == crlf
    assert with_privacy("[General]") == "[General]\nPrivacy = device\n"
    assert with_privacy("[General]\nPrivacy = off") == "[General]\nPrivacy = device"


def test_only_a_setting_that_keeps_an_advert_private_is_ever_written() -> None:
    assert "off" not in PRIVATE
    # the limited modes show the identity address while discoverable, and a passport's advert
    # always is
    for kept_out in ("off", "limited-device", "limited-network"):
        with pytest.raises(ValueError):
            with_privacy(STOCK, kept_out)
        assert privacy_words(kept_out, "public")[0] is False
    assert privacy_setting(with_privacy(STOCK, "network")) == "network"


def test_installing_backs_up_once_installs_as_root_and_restarts_bluetoothd() -> None:
    first = install_commands("/tmp/main.conf.new", backup_exists=False)
    assert first[0] == ["sudo", "cp", "-p", MAIN_CONF, BACKUP]
    assert [
        "sudo",
        "install",
        "-m",
        "0644",
        "-o",
        "root",
        "-g",
        "root",
        "/tmp/main.conf.new",
        MAIN_CONF,
    ] in first
    assert first[-1] == ["sudo", "systemctl", "restart", "bluetooth"]
    again = install_commands("/tmp/main.conf.new", backup_exists=True)
    assert not any("cp" in c for c in again)  # never over the backup of the original


def test_uninstall_restores_only_a_file_still_as_quackd_left_it() -> None:
    assert should_restore(with_privacy(STOCK), STOCK)
    assert not should_restore(with_privacy(STOCK) + "# mine\n", STOCK)
    assert not should_restore(with_privacy(STOCK), None)
    commands = restore_commands()
    assert commands[0][-2:] == [BACKUP, MAIN_CONF] and commands[-1][-1] == "bluetooth"


def test_the_words_never_claim_more_than_can_be_seen() -> None:
    private, words = privacy_words(None, "public")
    assert not private and "does not stop tracking" in words and "Privacy = device" in words
    private, words = privacy_words("device", "public")
    assert not private and "cannot be seen without root" in words
    private, words = privacy_words("device", "random")
    assert private and "cannot be seen without root" in words
