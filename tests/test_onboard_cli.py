"""`quackd onboard` at the command line: the wizard run from a file the way a script runs it, and
every subcommand that works without a service running.

The service's own behaviour, two robots meeting, is tests/test_onboard_service.py's. Here each
command is run as a person types it, against a robot directory in a temporary folder, on the sim
board, since no test machine is a Jetson.
"""

from __future__ import annotations

import contextlib
import json
import os
from importlib import resources
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quackd.cli import app
from quackd.onboard import config
from quackd.passport import document

runner = CliRunner()
PASSPHRASE = "a passphrase for these tests only"
COURIER = "g1-edu-mock"


def flat(output: str) -> str:
    return " ".join(output.split())


@pytest.fixture
def robot_file(tmp_path: Path) -> Path:
    """The demo's courier as a robot file a person handed the wizard, with the scripted pilot."""
    text = (resources.files("quackd.onboard") / "demo" / f"{COURIER}.yaml").read_text(
        encoding="utf-8"
    )
    path = tmp_path / "courier.yaml"
    path.write_text(text.replace("\nmeeting:\n", '\nbrain:\n  llm: "fake"\n\nmeeting:\n', 1))
    return path


@pytest.fixture
def secret(tmp_path: Path) -> Path:
    path = tmp_path / "secrets" / "passphrase.txt"
    path.parent.mkdir()
    path.write_text(PASSPHRASE + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path


def onboard(home: Path, robot_file: Path, secret: Path, *more: str) -> object:
    return runner.invoke(
        app,
        [
            "onboard",
            "--board",
            "sim",
            "--dir",
            str(home),
            "--file",
            str(robot_file),
            "--passphrase-file",
            str(secret),
            "--no-service",
            *more,
        ],
    )


def test_the_wizard_from_a_file_writes_the_robot_signs_it_and_says_how_to_start_it(
    tmp_path: Path, robot_file: Path, secret: Path
) -> None:
    home = tmp_path / "robot"
    first = onboard(home, robot_file, secret)
    assert first.exit_code == 0, first.output
    said = flat(first.output)
    assert "sim" in said and "signed" in said
    # a long path wraps at the terminal's width, so the command is read without its spaces
    assert f"quackdonboardserve--dir{home}--boardsim" in "".join(first.output.split())
    reading = document.read_jws((home / "passport.jws").read_text(encoding="utf-8").strip())
    assert reading.name == COURIER and reading.board()["kind"] == "sim"
    assert reading.owner_id != reading.id
    again = onboard(home, robot_file, secret)
    assert again.exit_code == 0, again.output
    assert "its passport is signed and current" in flat(again.output)


def test_the_wizard_refuses_root(
    tmp_path: Path, robot_file: Path, secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    refused = onboard(tmp_path / "robot", robot_file, secret)
    assert refused.exit_code == 1 and "not as root" in flat(refused.output)
    assert not (tmp_path / "robot" / "robot.yaml").exists()


def test_a_laptop_is_no_robot_and_is_told_how_to_try_it_as_a_simulation(tmp_path: Path) -> None:
    refused = runner.invoke(app, ["onboard", "--dir", str(tmp_path / "robot")])
    if refused.exit_code == 0:  # pragma: no cover - a test machine that is a Jetson
        pytest.skip("this machine is a robot's own computer")
    said = flat(refused.output)
    assert refused.exit_code == 1 and "--board sim" in said


def test_show_prints_the_passport_and_sign_signs_an_edit_again(
    tmp_path: Path, robot_file: Path, secret: Path
) -> None:
    home = tmp_path / "robot"
    assert onboard(home, robot_file, secret).exit_code == 0
    shown = runner.invoke(app, ["onboard", "show", "--dir", str(home), "--json"])
    assert shown.exit_code == 0, shown.output
    view = json.loads(shown.output)
    assert view["name"] == COURIER and view["board"]["kind"] == "sim"
    jws = runner.invoke(app, ["onboard", "show", "--dir", str(home), "--jws"])
    assert jws.output.strip() == (home / "passport.jws").read_text(encoding="utf-8").strip()
    yaml = home / "robot.yaml"
    yaml.write_text(
        yaml.read_text(encoding="utf-8").replace("Simulated: stop the process.", "Pull its cord."),
        encoding="utf-8",
    )
    signed = runner.invoke(
        app,
        ["onboard", "sign", "--board", "sim", "--dir", str(home), "--passphrase-file", str(secret)],
    )
    assert signed.exit_code == 0, signed.output
    reading = document.read_jws((home / "passport.jws").read_text(encoding="utf-8").strip())
    assert reading.document["safety"]["how_to_stop"] == "Pull its cord."


def test_pin_trusts_a_robot_met_and_forget_takes_it_back(
    tmp_path: Path, robot_file: Path, secret: Path
) -> None:
    home = tmp_path / "robot"
    assert onboard(home, robot_file, secret).exit_code == 0
    other = "did:key:z6MkehRgf7yJbgaGfYsdoAsKdBPE3dj2CYhowQdcjqSJgvVd"
    owner = "did:key:z6MkhFwXNFWosLeugvSf4wcL9t3uuRXueGSFTRgSvHhWj5G2"
    row = {
        "at": "2026-10-29T10:00:00Z",
        "id": other,
        "name": "agile-one-sn-mock",
        "shown": "stranger-56475aa7",
        "tier": "stranger",
        "short_id": "56475aa75463474c",
        "owner_id": owner,
        "owner_label": "Household (demo)",
        "via": "ble",
    }
    (home / "met.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    met = runner.invoke(app, ["onboard", "met", "--dir", str(home)])
    assert met.exit_code == 0 and "agile-one-sn-mock" in met.output
    pinned = runner.invoke(app, ["onboard", "pin", "5647", "--owner", "--yes", "--dir", str(home)])
    assert pinned.exit_code == 0, pinned.output
    pins = config.load(home / "robot.yaml").trust.pins
    assert [(p.kind, p.id, p.name) for p in pins] == [("owner", owner, "Household (demo)")]
    forgot = runner.invoke(app, ["onboard", "forget", "Household (demo)", "--dir", str(home)])
    assert forgot.exit_code == 0, forgot.output
    assert config.load(home / "robot.yaml").trust.pins == []
    nobody = runner.invoke(app, ["onboard", "pin", "beef", "--yes", "--dir", str(home)])
    assert nobody.exit_code == 1 and "met no robot" in flat(nobody.output)


def test_preflight_reads_the_sim_board_and_the_robot_and_changes_nothing(
    tmp_path: Path, robot_file: Path, secret: Path
) -> None:
    home = tmp_path / "robot"
    assert onboard(home, robot_file, secret).exit_code == 0
    before = sorted(p.name for p in home.iterdir())
    checked = runner.invoke(
        app, ["onboard", "preflight", "--board", "sim", "--dir", str(home), "--json"]
    )
    assert checked.exit_code == 0, checked.output
    found = json.loads(checked.output)
    states = {row["check"]: row["state"] for row in found["checks"]}
    assert found["ok"] is True
    assert states["board"] == states["sim air"] == states["passport"] == states["brain"] == "ok"
    assert states["service"] == "warn"
    assert sorted(p.name for p in home.iterdir()) == before
    empty = runner.invoke(
        app, ["onboard", "preflight", "--board", "sim", "--dir", str(tmp_path / "x"), "--json"]
    )
    assert empty.exit_code == 1 and json.loads(empty.output)["ok"] is False


def test_what_needs_a_service_says_none_is_running(
    tmp_path: Path, robot_file: Path, secret: Path
) -> None:
    home = tmp_path / "robot"
    assert onboard(home, robot_file, secret).exit_code == 0
    for words in (["status"], ["stop"], ["goal", "say hello"]):
        answered = runner.invoke(app, ["onboard", *words, "--dir", str(home)])
        assert answered.exit_code == 1, (words, answered.output)
    served = runner.invoke(app, ["onboard", "serve", "--board", "sim", "--dir", str(tmp_path)])
    assert served.exit_code == 1 and "no robot here" in flat(served.output)


def test_the_demo_asks_which_brain_before_it_starts_anything(tmp_path: Path) -> None:
    asked = runner.invoke(app, ["onboard", "demo", "--keep", str(tmp_path / "kept")])
    assert asked.exit_code == 1 and "--offline" in flat(asked.output)
    unknown = runner.invoke(app, ["onboard", "demo", "--offline", "--act", "dance"])
    assert unknown.exit_code == 1 and "no act called 'dance'" in flat(unknown.output)
    assert not (tmp_path / "kept").exists()


def test_a_robot_pin_names_its_owner_and_a_warning_reaches_met_and_pin(
    tmp_path: Path, robot_file: Path, secret: Path
) -> None:
    """`pin` writes the owner that signed the robot, so another owner signing for that robot key
    makes it a stranger again; and what the judge warned of is shown where a person decides."""
    home = tmp_path / "robot"
    assert onboard(home, robot_file, secret).exit_code == 0
    other = "did:key:z6MkehRgf7yJbgaGfYsdoAsKdBPE3dj2CYhowQdcjqSJgvVd"
    owner = "did:key:z6MkhFwXNFWosLeugvSf4wcL9t3uuRXueGSFTRgSvHhWj5G2"
    warning = "a robot named cart-01 was pinned with another key"
    row = {
        "at": "2026-10-29T10:00:00Z",
        "id": other,
        "name": "cart-0l",
        "shown": "stranger-56475aa7",
        "tier": "stranger",
        "short_id": "56475aa75463474c",
        "owner_id": owner,
        "owner_label": "Somebody",
        "via": "ble",
        "warn": [warning],
    }
    (home / "met.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    met = runner.invoke(app, ["onboard", "met", "--dir", str(home)])
    # in a cell of the table, which wraps it between the borders
    assert met.exit_code == 0 and "another key" in flat(met.output)
    pinned = runner.invoke(app, ["onboard", "pin", "5647", "--yes", "--dir", str(home)])
    assert pinned.exit_code == 0 and warning in flat(pinned.output)
    pins = config.load(home / "robot.yaml").trust.pins
    assert [(p.kind, p.id, p.owner) for p in pins] == [("robot", other, owner)]
