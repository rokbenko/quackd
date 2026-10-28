"""Signing a robot's passport, deciding when to sign again, the service's own check of what it
serves, and the systemd unit that keeps it running.

The owner key is sealed with a passphrase, so every signing here reads one from a file, as a
script would; a terminal's prompt is the wizard's business and is left out.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quackd.onboard import config, systemd, wizard
from quackd.onboard.keys import WrongPassphrase
from quackd.onboard.service import check_own_passport
from quackd.onboard.store import OnboardDir
from quackd.passport import document

SIM = {"kind": "sim", "model": "a computer standing in for a robot's own"}
PASSPHRASE = "correct horse battery staple"


class NoQuestions:
    def text(self, question: str, default: str | None = None) -> str:
        return default or ""

    def secret(self, question: str, *, twice: bool = False) -> str:
        raise AssertionError("a test reads its passphrase from a file")

    def yes(self, question: str, default: bool = False) -> bool:
        return default


def robot_text(**overrides: object) -> str:
    fields: dict[str, object] = {
        "name": "g1-edu-01",
        "vendor": "unitree",
        "model": "g1-edu",
        "owner_label": "Example lab",
        "owner_contact": None,
        "embodiment": "humanoid",
        "mobility": "legged",
        "how_to_stop": "the red button on its remote",
        "estop": "remote",
    }
    fields.update(overrides)
    return config.template(**fields)  # type: ignore[arg-type]


@pytest.fixture
def home(tmp_path: Path) -> OnboardDir:
    made = OnboardDir(tmp_path / "robot")
    made.ensure()
    made.robot_yaml.write_text(robot_text(), encoding="utf-8")
    return made


@pytest.fixture
def passphrase_file(tmp_path: Path) -> Path:
    path = tmp_path / "secrets" / "passphrase.txt"
    path.parent.mkdir()
    path.write_text(PASSPHRASE + "\n", encoding="utf-8")
    with __import__("contextlib").suppress(OSError):
        os.chmod(path, 0o600)
    return path


def sign(home: OnboardDir, passphrase_file: Path, days: int = 365) -> wizard.Signed:
    return wizard.sign(
        home,
        config.load(home.robot_yaml),
        SIM,
        ask=NoQuestions(),
        passphrase_file=passphrase_file,
        days=days,
    )


def test_the_first_signing_makes_both_keys_and_a_passport_its_owner_signed(
    home: OnboardDir, passphrase_file: Path
) -> None:
    signed = sign(home, passphrase_file)
    assert signed.robot_key_made and signed.owner_key_made
    reading = document.read_jws(signed.token)
    assert reading.signed and reading.board() == SIM
    assert reading.owner_id != reading.id
    assert home.passport_jws.read_text(encoding="utf-8").strip() == signed.token
    again = sign(home, passphrase_file)
    assert not again.robot_key_made and not again.owner_key_made
    assert document.read_jws(again.token).id == reading.id


def test_signing_again_needs_the_passphrase_the_owner_key_was_sealed_with(
    home: OnboardDir, passphrase_file: Path, tmp_path: Path
) -> None:
    sign(home, passphrase_file)
    wrong = tmp_path / "secrets" / "wrong.txt"
    wrong.write_text("not the passphrase at all\n", encoding="utf-8")
    with __import__("contextlib").suppress(OSError):
        os.chmod(wrong, 0o600)
    with pytest.raises(WrongPassphrase):
        sign(home, wrong)


def test_a_signed_passport_is_signed_again_only_when_its_file_changed_or_its_days_run_out(
    home: OnboardDir, passphrase_file: Path
) -> None:
    never = wizard.needs_signing(home, config.load(home.robot_yaml), SIM)
    assert never == "it has never been signed"
    sign(home, passphrase_file)
    assert wizard.needs_signing(home, config.load(home.robot_yaml), SIM) is None
    home.robot_yaml.write_text(robot_text(how_to_stop="pull the cord"), encoding="utf-8")
    why = wizard.needs_signing(home, config.load(home.robot_yaml), SIM)
    assert why is not None and "safety" in why
    sign(home, passphrase_file, days=wizard.RENEW_WITHIN_DAYS - 1)
    why = wizard.needs_signing(home, config.load(home.robot_yaml), SIM)
    assert why is not None and "runs out" in why


def test_the_service_serves_only_a_passport_that_checks_out_for_its_own_key(
    home: OnboardDir, passphrase_file: Path, tmp_path: Path
) -> None:
    from quackd.onboard.keys import read_robot_key

    assert not check_own_passport(None, "did:key:x").ok
    signed = sign(home, passphrase_file)
    robot = read_robot_key(home.robot_key)
    assert check_own_passport(signed.token, robot.did).ok
    other = OnboardDir(tmp_path / "another")
    other.ensure()
    other.robot_yaml.write_text(robot_text(name="g1-edu-02"), encoding="utf-8")
    elsewhere = sign(other, passphrase_file)
    check = check_own_passport(elsewhere.token, robot.did)
    assert not check.ok and "another robot key" in check.why
    late = int((datetime.now(UTC) + timedelta(days=400)).timestamp())
    expired = check_own_passport(signed.token, robot.did, now=late)
    assert not expired.ok and "expired" in expired.why


def test_the_unit_runs_quackd_as_the_robots_user_and_writes_only_its_directory(
    tmp_path: Path,
) -> None:
    quackd = "/home/robot/.local/bin/quackd"
    text = systemd.unit_text(user="robot", directory=tmp_path, quackd=quackd)
    assert "User=robot" in text and "SupplementaryGroups=bluetooth" in text
    assert f"ReadWritePaths={tmp_path}" in text and "ProtectSystem=strict" in text
    assert "ExecStart=/home/robot/.local/bin/quackd onboard serve" in text
    assert f"EnvironmentFile=-{tmp_path}/robot.env" in text
    bare = systemd.unit_text(user="robot", directory=tmp_path, quackd="q", bluetooth_group=False)
    assert "SupplementaryGroups" not in bare
    commands = systemd.install_commands(tmp_path / "unit")
    assert all(command[0] == "sudo" for command in commands)
    assert commands[-1][-1] == systemd.UNIT_NAME


@pytest.mark.skipif(sys.platform == "win32", reason="Windows files have no POSIX modes")
def test_the_brains_key_is_written_for_its_user_alone(tmp_path: Path) -> None:
    path = tmp_path / "robot.env"
    wizard.write_key_file(path, "LOCAL_API_KEY", "secret-value")
    assert path.read_text(encoding="utf-8") == "LOCAL_API_KEY=secret-value\n"
    assert (path.stat().st_mode & 0o077) == 0
    with pytest.raises(wizard.WizardError):
        wizard.write_key_file(path, "LOCAL_API_KEY", "two\nlines")
