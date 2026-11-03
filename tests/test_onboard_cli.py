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
import subprocess
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd import boards
from quackd.adapters.base import AdapterNotInstalled
from quackd.boards import generic
from quackd.boards.linux import BLUETOOTH
from quackd.cli import _status_rows, app
from quackd.onboard import config, demo, radio_side, service
from quackd.onboard import facts as facts_module
from quackd.onboard import preflight as preflight_module
from quackd.onboard.store import OnboardDir
from quackd.passport import document
from quackd.radio import NoRadio
from tests.linux_board_fixtures import DRAGON_Q6A, UBUNTU_24

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


@pytest.fixture
def named_computer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The generic board on any machine: Linux to that board alone, and a Radxa Dragon Q6A's files
    to read, so no test here reads the computer it runs on."""
    files = DRAGON_Q6A | UBUNTU_24
    monkeypatch.setattr(generic, "system_name", lambda: "Linux")
    monkeypatch.setattr(generic, "read_file", files.get)


def onboard_named(home: Path, robot_file: Path, secret: Path) -> Any:
    return runner.invoke(
        app,
        [
            "onboard",
            "--board",
            "generic",
            "--dir",
            str(home),
            "--file",
            str(robot_file),
            "--passphrase-file",
            str(secret),
            "--no-service",
        ],
    )


def signed_board(home: Path) -> dict[str, Any]:
    token = (home / "passport.jws").read_text(encoding="utf-8").strip()
    return document.read_jws(token).board()


def test_a_board_named_once_is_remembered_so_nothing_later_needs_the_flag(
    tmp_path: Path, robot_file: Path, secret: Path, named_computer: None
) -> None:
    """The unit runs `quackd onboard serve`, and every hint says `quackd onboard sign`, with no
    flag. A board only ever found by name would refuse them all, and point at `--board sim`."""
    home = tmp_path / "robot"
    first = onboard_named(home, robot_file, secret)
    assert first.exit_code == 0, first.output
    said = flat(first.output)
    assert "Radxa Dragon Q6A (other, Ubuntu 24.04.4 LTS)" in said
    assert "--board" not in said, "the line that says how to start it needs no flag"
    assert (home / "board").read_text(encoding="utf-8") == "generic\n"
    assert signed_board(home) == {"kind": "other", "model": "Radxa Dragon Q6A", "soc": "qcm6490"}
    signed = runner.invoke(
        app, ["onboard", "sign", "--dir", str(home), "--passphrase-file", str(secret)]
    )
    assert signed.exit_code == 0, signed.output
    assert signed_board(home)["kind"] == "other"
    checked = runner.invoke(app, ["onboard", "preflight", "--dir", str(home), "--json"])
    rows = {row["check"]: row for row in json.loads(checked.output)["checks"]}
    assert rows["board"]["state"] == "ok" and "Radxa Dragon Q6A" in rows["board"]["detail"]
    assert rows["passport"]["state"] == "ok"
    start = "sudo systemctl start quackd-onboard, or quackd onboard serve"
    assert rows["service"]["hint"] == start
    again = runner.invoke(
        app,
        [
            "onboard",
            "--dir",
            str(home),
            "--file",
            str(robot_file),
            "--passphrase-file",
            str(secret),
            "--no-service",
        ],
    )
    assert again.exit_code == 0, again.output
    assert "its passport is signed and current" in flat(again.output)


def test_the_service_finds_the_remembered_board_as_the_unit_starts_it_with_no_flag(
    tmp_path: Path,
    robot_file: Path,
    secret: Path,
    named_computer: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "robot"
    assert onboard_named(home, robot_file, secret).exit_code == 0
    asked: list[str | None] = []

    def detect(name: str | None = None, **_: object) -> Any:
        asked.append(name)
        raise boards.NotARobot("that is all this test wanted to know")

    monkeypatch.setattr(boards, "detect", detect)
    served = runner.invoke(app, ["onboard", "serve", "--dir", str(home)])
    assert served.exit_code == 1 and "all this test wanted" in flat(served.output)
    assert asked == ["generic"]


def test_a_flag_wins_over_the_remembered_board_and_sim_is_never_remembered(
    tmp_path: Path, robot_file: Path, secret: Path, named_computer: None
) -> None:
    """Sim is named every time, so no file can make a robot take itself for a simulation."""
    home = tmp_path / "robot"
    assert onboard(home, robot_file, secret).exit_code == 0
    assert not (home / "board").exists()
    kept = OnboardDir(home)
    assert kept.board_named() is None
    kept.remember_board("sim")
    assert not kept.board_file.exists()
    kept.board_file.write_text("sim\n", encoding="utf-8")
    assert kept.board_named() is None, "a file that says sim by hand says nothing"
    kept.board_file.write_text(" Generic \n", encoding="utf-8")
    assert kept.board_named() == "generic"
    assert kept.board_named("jetson") == "jetson"
    kept.board_file.write_bytes(bytes([255, 254]))
    assert kept.board_named() is None, "a file that is no text says nothing"


def test_the_wizard_on_another_board_forgets_the_one_it_remembered(
    tmp_path: Path, robot_file: Path, secret: Path, named_computer: None
) -> None:
    home = tmp_path / "robot"
    assert onboard_named(home, robot_file, secret).exit_code == 0
    assert (home / "board").exists()
    assert onboard(home, robot_file, secret).exit_code == 0
    assert not (home / "board").exists()


def test_a_remembered_board_nothing_provides_says_so(
    tmp_path: Path, robot_file: Path, secret: Path
) -> None:
    home = tmp_path / "robot"
    assert onboard(home, robot_file, secret).exit_code == 0
    (home / "board").write_text("gone-plugin\n", encoding="utf-8")
    signed = runner.invoke(
        app, ["onboard", "sign", "--dir", str(home), "--passphrase-file", str(secret)]
    )
    assert signed.exit_code == 1 and "no board called 'gone-plugin'" in flat(signed.output)


def test_the_service_opens_the_adapter_preflight_checked(
    tmp_path: Path,
    robot_file: Path,
    secret: Path,
    named_computer: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preflight checks the first adapter the kernel lists, and the radio opened `hci0`, so a
    computer whose only adapter came up as `hci1` passed preflight and got no radio."""
    home = tmp_path / "robot"
    assert onboard_named(home, robot_file, secret).exit_code == 0
    monkeypatch.setattr(generic, "list_dir", {BLUETOOTH: ["hci1"]}.get)
    monkeypatch.setattr(service, "_browser", lambda log: None)

    class Opened(Exception):
        pass

    def side(**asked: Any) -> Any:
        raise Opened(asked["adapter"])

    monkeypatch.setattr(service, "board_side", side)
    with pytest.raises(Opened, match="hci1"):
        service.serve(OnboardDir(home), board=None, log=lambda line: None)


@pytest.mark.parametrize(
    ("kind", "adapter", "wanted"),
    [
        ("bluez", "hci1", {"advertise": True, "adapter": "hci1"}),
        ("bluez", None, {"advertise": True}),
        ("bluez", "sim", {"advertise": True}),
        ("off", "hci1", {}),
    ],
    ids=["found", "none-found", "not-an-adapter", "radio-off"],
)
def test_the_radio_is_opened_on_the_adapter_it_is_handed(
    monkeypatch: pytest.MonkeyPatch, kind: str, adapter: str | None, wanted: dict[str, Any]
) -> None:
    asked: dict[str, Any] = {}

    def opened(radio_kind: str, **options: Any) -> Any:
        asked.update(options)
        return NoRadio()

    monkeypatch.setattr(radio_side, "open_radio", opened)
    radio_side.board_side(radio_kind=kind, instance="x", adapter=adapter)
    assert asked == wanted


@pytest.fixture
def body_file(robot_file: Path) -> Path:
    """A robot file that names a body quackd drives, whose manifest supplies what it can do."""
    text = robot_file.read_text(encoding="utf-8")
    body = text.replace("\nmeeting:\n", "\nrobot:\n  adapter: microduck:sim2d\n\nmeeting:\n", 1)
    assert body != text
    robot_file.write_text(body, encoding="utf-8")
    return robot_file


def test_a_robot_with_a_body_does_not_read_as_unsigned_just_after_it_was_signed(
    tmp_path: Path, body_file: Path, secret: Path
) -> None:
    """Signing reads the body's manifest, and preflight and status compared the passport with
    the file alone, so every member the manifest supplies looked changed."""
    home = tmp_path / "robot"
    assert onboard(home, body_file, secret).exit_code == 0
    signed = document.read_jws((home / "passport.jws").read_text(encoding="utf-8").strip())
    assert signed.document["body"]["mobility"] == "legged", "the manifest said what it is"
    _, rows = preflight_rows(home, "--board", "sim")
    assert rows["passport"]["state"] == "ok"
    assert "signed" not in rows, rows.get("signed")
    reply = {
        "passport": {"ok": True, "why": "its passport checks out"},
        "board": signed.board(),
        "radio": {},
        "run": {},
    }
    assert "unsigned" not in [name for name, _ in _status_rows(OnboardDir(home), reply)]


def test_a_body_that_cannot_be_read_here_is_said_and_stops_neither_preflight_nor_status(
    tmp_path: Path, body_file: Path, secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "robot"
    assert onboard(home, body_file, secret).exit_code == 0
    signed = document.read_jws((home / "passport.jws").read_text(encoding="utf-8").strip())

    def not_here(file: Any) -> Any:
        raise AdapterNotInstalled("microduck", "quackd[microduck]")

    monkeypatch.setattr(facts_module, "manifest_of", not_here)
    _, rows = preflight_rows(home, "--board", "sim")
    assert rows["signed"]["state"] == "warn" and "quackd[microduck]" in rows["signed"]["detail"]
    reply = {"passport": {"ok": True}, "board": signed.board(), "radio": {}, "run": {}}
    assert "unsigned" not in [name for name, _ in _status_rows(OnboardDir(home), reply)]


@pytest.mark.parametrize(
    ("before", "command", "said"),
    [
        (["--dir", "somewhere"], ["sign"], "--dir goes after the command"),
        (["--board", "sim"], ["sign"], "--board goes after the command"),
        (["--days", "30"], ["sign"], "--days goes after the command"),
        (["--board", "sim"], ["status"], "takes no --board"),
        (["--file", "robot.yaml"], ["sign"], "takes no --file"),
        (["--yes"], ["met"], "takes no --yes"),
    ],
    ids=["dir", "board", "days", "no-such-flag", "wizards-file", "wizards-yes"],
)
def test_a_flag_before_a_command_is_refused_and_not_dropped(
    before: list[str], command: list[str], said: str
) -> None:
    """`quackd onboard --dir ~/two sign` parsed, reached nothing, and signed the robot in the
    default folder."""
    refused = runner.invoke(app, ["onboard", *before, *command])
    assert refused.exit_code == 1
    assert said in flat(refused.output)


def test_a_flag_before_a_command_signs_nothing(
    tmp_path: Path, robot_file: Path, secret: Path
) -> None:
    home, other = tmp_path / "robot", tmp_path / "other"
    assert onboard(home, robot_file, secret).exit_code == 0
    before = (home / "passport.jws").read_bytes()
    refused = runner.invoke(
        app,
        [
            "onboard",
            "--dir",
            str(other),
            "sign",
            "--dir",
            str(home),
            "--board",
            "sim",
            "--passphrase-file",
            str(secret),
        ],
    )
    assert refused.exit_code == 1
    assert (home / "passport.jws").read_bytes() == before and not other.exists()


def test_a_flag_after_a_command_and_a_flag_with_no_command_still_work(
    tmp_path: Path, robot_file: Path, secret: Path
) -> None:
    home = tmp_path / "robot"
    assert onboard(home, robot_file, secret).exit_code == 0
    signed = runner.invoke(
        app,
        [
            "onboard",
            "sign",
            "--board",
            "sim",
            "--dir",
            str(home),
            "--passphrase-file",
            str(secret),
            "--days",
            "30",
        ],
    )
    assert signed.exit_code == 0, signed.output
    bare = runner.invoke(app, ["onboard", "status", "--dir", str(home)])
    assert "goes after the command" not in flat(bare.output)


def test_a_robot_on_a_board_with_no_kind_of_its_own_is_not_named_other_by_default(
    tmp_path: Path, secret: Path, named_computer: None
) -> None:
    home = tmp_path / "robot"
    made = runner.invoke(
        app,
        [
            "onboard",
            "--board",
            "generic",
            "--dir",
            str(home),
            "--yes",
            "--passphrase-file",
            str(secret),
            "--no-service",
        ],
    )
    assert made.exit_code == 0, made.output
    assert config.load(home / "robot.yaml").passport.name == "robot-01"


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


def preflight_rows(home: Path, *board: str) -> tuple[int, dict[str, dict[str, str]]]:
    checked = runner.invoke(app, ["onboard", "preflight", *board, "--dir", str(home), "--json"])
    return checked.exit_code, {row["check"]: row for row in json.loads(checked.output)["checks"]}


THE_CLOCK = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("synced", "rtc", "state", "words"),
    [
        (True, True, "ok", None),
        (None, None, "ok", None),
        (True, None, "ok", None),
        (False, True, "warn", "nothing has set it"),
        (False, False, "warn", "nothing has set it"),
        (False, None, "warn", "nothing has set it"),
        (True, False, "warn", "no clock here that survives a reboot"),
        (None, False, "warn", "no clock here that survives a reboot"),
    ],
)
def test_the_clock_row_says_whether_anything_keeps_the_clock_right(
    synced: bool | None, rtc: bool | None, state: str, words: str | None
) -> None:
    check = preflight_module._clock(THE_CLOCK, synced, rtc)
    assert check.state == state and check.detail.startswith("2026-11-03 12:00 UTC")
    assert words is None or words in check.detail
    assert (check.hint is None) == (words is None)


def test_a_clock_before_any_passport_fails_whatever_else_it_says() -> None:
    long_ago = THE_CLOCK.replace(year=preflight_module.EARLIEST_YEAR - 1)
    check = preflight_module._clock(long_ago, True, True)
    assert check.state == "fail" and check.hint == preflight_module.SET_THE_TIME


@pytest.mark.parametrize(
    ("code", "stdout", "listed", "wanted"),
    [
        (0, "yes\n", ["rtc0"], (True, True)),
        (0, "no\n", [], (False, False)),
        (1, "", None, (None, None)),
        (0, "something else\n", ["rtc0"], (None, True)),
    ],
    ids=["synced", "unsynced-no-rtc", "no-systemd-no-folder", "unreadable"],
)
def test_what_linux_says_of_its_clock_is_read_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    stdout: str,
    listed: list[str] | None,
    wanted: tuple[bool | None, bool | None],
) -> None:
    asked: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        asked.append(command)
        return subprocess.CompletedProcess(command, code, stdout, "")

    monkeypatch.setattr(preflight_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(preflight_module.subprocess, "run", run)
    monkeypatch.setattr(preflight_module, "list_dir", lambda path: listed)
    assert preflight_module._clock_state() == wanted
    assert asked == [["timedatectl", "show", "--property=NTPSynchronized", "--value"]]


def test_a_computer_with_no_timedatectl_says_nothing_of_it_and_off_linux_nothing_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("timedatectl")

    monkeypatch.setattr(preflight_module.subprocess, "run", run)
    monkeypatch.setattr(preflight_module, "list_dir", lambda path: ["rtc0"])
    monkeypatch.setattr(preflight_module.platform, "system", lambda: "Linux")
    assert preflight_module._clock_state() == (None, True)
    monkeypatch.setattr(preflight_module.platform, "system", lambda: "Windows")
    assert preflight_module._clock_state() == (None, None)


def test_preflight_off_a_robot_leaves_the_next_step_to_the_board_row(
    tmp_path: Path, robot_file: Path, secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where no board knows the computer, serve refuses just as the board's row does, so the
    service's row names no command of its own; and a robot with no brain is told where one goes."""
    no_brain = tmp_path / "no-brain.yaml"
    text = robot_file.read_text(encoding="utf-8")
    no_brain.write_text(text.replace('\nbrain:\n  llm: "fake"\n', "\n", 1), encoding="utf-8")
    home = tmp_path / "robot"
    assert onboard(home, no_brain, secret).exit_code == 0

    def no_board(name: str | None = None, **_: object) -> object:
        raise boards.NotARobot("it found no NVIDIA Jetson")

    monkeypatch.setattr(boards, "detect", no_board)
    code, rows = preflight_rows(home)
    assert code == 1 and rows["board"]["state"] == "fail"
    assert rows["service"] == {"check": "service", "state": "warn", "detail": "not running here"}
    assert rows["brain"]["state"] == "warn"
    assert rows["brain"]["hint"] == f"set brain.llm in {home / 'robot.yaml'}"


def test_preflight_on_a_jetson_offers_the_terminal_beside_the_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Jetson set up with --no-service has no unit to start, so the service's row offers
    `quackd onboard serve` beside it, as `status` and `goal` do."""
    from quackd.onboard.preflight import preflight
    from quackd.onboard.store import OnboardDir

    class Jetson:
        name = "jetson"
        explicit_only = False
        radio_kind = "bluez"

        def radio(self) -> boards.RadioCheck:
            return boards.RadioCheck(ok=True, adapter="hci0")

    facts = boards.BoardFacts(kind="jetson", model="NVIDIA Jetson AGX Thor", soc="tegra264")
    monkeypatch.setattr(boards, "detect", lambda name=None, **_: (Jetson(), facts))
    checks = preflight(OnboardDir(tmp_path), None, service=lambda: None, radio_rows=lambda *_: [])
    service = {check.name: check for check in checks}["service"]
    assert service.hint == "sudo systemctl start quackd-onboard, or quackd onboard serve"


def test_a_brain_on_a_server_of_your_own_needs_no_key(
    tmp_path: Path, robot_file: Path, secret: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wizard writes no robot.env for a server needing none, and a local preset sends the word
    `not-needed` then, so preflight passes that brain and the demo starts with it; a hosted brain
    with no key still fails both. Empty reads as unset, and survives a developer's `.env`."""
    for name in ("LOCAL_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(name, "")
    local = tmp_path / "local.yaml"
    text = robot_file.read_text(encoding="utf-8")
    local.write_text(text.replace('llm: "fake"', 'llm: "ollama:qwen3:8b"', 1), encoding="utf-8")
    home = tmp_path / "robot"
    assert onboard(home, local, secret, "--yes").exit_code == 0
    assert not (home / "robot.env").exists()
    code, rows = preflight_rows(home, "--board", "sim")
    assert code == 0 and rows["brain"]["state"] == "ok"
    assert "no LOCAL_API_KEY" in rows["brain"]["detail"]
    yaml = home / "robot.yaml"
    hosted = yaml.read_text(encoding="utf-8").replace("ollama:qwen3:8b", "gemini:gemini-3-flash")
    yaml.write_text(hosted, encoding="utf-8")
    code, rows = preflight_rows(home, "--board", "sim")
    assert code == 1 and rows["brain"]["state"] == "fail"
    assert "GEMINI_API_KEY" in rows["brain"]["detail"]
    started: list[str] = []
    monkeypatch.setattr(demo, "run_demo", lambda **kw: started.append(kw["llm"]) or [])
    ran = runner.invoke(app, ["onboard", "demo", "--llm", "ollama:qwen3:8b", "--json"])
    assert ran.exit_code == 0, ran.output
    refused = runner.invoke(app, ["onboard", "demo", "--llm", "gemini:gemini-3-flash"])
    assert refused.exit_code == 1 and "needs GEMINI_API_KEY" in flat(refused.output)
    assert started == ["ollama:qwen3:8b"]


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


def test_a_name_another_robot_chose_reaches_the_terminal_escaped(
    tmp_path: Path, robot_file: Path, secret: Path
) -> None:
    home = tmp_path / "robot"
    assert onboard(home, robot_file, secret).exit_code == 0
    row = {
        "at": "2026-10-29T10:00:00Z",
        "id": "did:key:z6MkehRgf7yJbgaGfYsdoAsKdBPE3dj2CYhowQdcjqSJgvVd",
        "name": "cart\x1b[2J\x07",
        "shown": "stranger-56475aa7",
        "tier": "stranger",
        "short_id": "56475aa75463474c",
        "owner_id": "did:key:z6MkhFwXNFWosLeugvSf4wcL9t3uuRXueGSFTRgSvHhWj5G2",
        "owner_label": "Somebody",
        "via": "ble",
    }
    (home / "met.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    pinned = runner.invoke(app, ["onboard", "pin", "5647", "--yes", "--dir", str(home)])
    assert pinned.exit_code == 0 and "\x1b" not in pinned.output and "\x07" not in pinned.output
    forgot = runner.invoke(app, ["onboard", "forget", "5647", "--dir", str(home)])
    assert forgot.exit_code == 0 and "\x1b" not in forgot.output
