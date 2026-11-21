"""Signing a robot's passport, deciding when to sign again, the service's own check of what it
serves, and the systemd unit that keeps it running.

The owner key is sealed with a passphrase, so every signing here reads one from a file, as a
script would; a terminal's prompt is the wizard's business and is left out.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from roboglot.onboard import config, systemd, wizard
from roboglot.onboard import preflight as preflight_module
from roboglot.onboard.keys import WrongPassphrase
from roboglot.onboard.service import check_own_passport
from roboglot.onboard.store import OnboardDir
from roboglot.passport import document

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


def test_what_a_computer_calls_itself_changing_is_not_robot_yaml_changing(
    home: OnboardDir, passphrase_file: Path
) -> None:
    """A firmware update can rename a computer, and nobody edited anything."""
    sign(home, passphrase_file)
    renamed = {**SIM, "model": "another name"}
    file = config.load(home.robot_yaml)
    assert wizard.needs_signing(home, file, renamed) == (
        "this computer calls itself something else since it was signed"
    )
    assert wizard.needs_signing(home, file, renamed, file_alone=True) == (
        "this computer calls itself something else since it was signed"
    ), "with no passport to build, the board is still the board"
    assert wizard.needs_signing(home, file, SIM, file_alone=True) is None
    home.robot_yaml.write_text(robot_text(how_to_stop="pull the cord"), encoding="utf-8")
    why = wizard.needs_signing(home, config.load(home.robot_yaml), renamed)
    assert why == "robot.yaml changed since it was signed: safety", "board is no edit of it"


def test_a_clock_before_any_passport_cannot_sign_and_makes_no_key_trying(
    home: OnboardDir, passphrase_file: Path
) -> None:
    """A board with no clock battery and no network wakes up long ago, and a signature from then
    would have run out before anybody read it."""
    long_ago = datetime(preflight_module.EARLIEST_YEAR - 1, 12, 31, tzinfo=UTC)
    with pytest.raises(wizard.WizardError, match="timedatectl"):
        wizard.sign(
            home,
            config.load(home.robot_yaml),
            SIM,
            ask=NoQuestions(),
            passphrase_file=passphrase_file,
            days=30,
            now=long_ago,
        )
    assert not home.robot_key.exists() and not home.owner_key.exists()
    assert not home.passport_jws.exists()


def test_a_clock_behind_the_passport_is_told_to_set_the_time_and_not_to_sign_again(
    home: OnboardDir, passphrase_file: Path
) -> None:
    """Signing there would write the wrong date down. Any other failure still says to sign."""

    def passport_row() -> preflight_module.Check:
        checks = preflight_module.preflight(
            home,
            "sim",
            service=lambda: None,
            radio_rows=lambda *_: [],
            clock_state=lambda: (None, None),
        )
        return next(check for check in checks if check.name == "passport")

    def signed_at(moment: datetime, days: int) -> None:
        wizard.sign(
            home,
            config.load(home.robot_yaml),
            SIM,
            ask=NoQuestions(),
            passphrase_file=passphrase_file,
            days=days,
            now=moment,
        )

    signed_at(datetime.now(UTC) + timedelta(days=30), 30)
    row = passport_row()
    assert row.state == "fail" and "clock is behind" in row.detail
    assert row.hint == preflight_module.SET_THE_TIME
    signed_at(datetime.now(UTC) - timedelta(days=30), 1)
    row = passport_row()
    assert row.state == "fail" and "expired" in row.detail
    assert row.hint == "roboglot onboard sign"


def test_the_service_serves_only_a_passport_that_checks_out_for_its_own_key(
    home: OnboardDir, passphrase_file: Path, tmp_path: Path
) -> None:
    from roboglot.onboard.keys import read_robot_key

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


def test_the_unit_runs_roboglot_as_the_robots_user_and_writes_only_its_directory(
    tmp_path: Path,
) -> None:
    roboglot = "/home/robot/.local/bin/roboglot"
    text = systemd.unit_text(user="robot", directory=tmp_path, roboglot=roboglot)
    assert "User=robot" in text and "SupplementaryGroups=bluetooth" in text
    assert f"ReadWritePaths={tmp_path}" in text and "ProtectSystem=strict" in text
    assert "ExecStart=/home/robot/.local/bin/roboglot onboard serve" in text
    assert f"EnvironmentFile=-{tmp_path}/robot.env" in text
    bare = systemd.unit_text(user="robot", directory=tmp_path, roboglot="q", bluetooth_group=False)
    assert "SupplementaryGroups" not in bare
    commands = systemd.install_commands(tmp_path / "unit")
    assert all(command[0] == "sudo" for command in commands)
    assert commands[-1][-1] == systemd.UNIT_NAME


#: The C escapes systemd's unquoting reads in `Environment=` and `ExecStart=`, beside a backslash
#: before a backslash or a quote, which is that character (systemd.syntax(5), "Quoting").
C_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "s": " "}


def systemd_words(value: str, *, c_escapes: bool) -> list[str]:
    """`value` split into words as systemd splits a list or a command line: at whitespace, a
    double or a single quote keeping all up to its match in one word, and a backslash, in quotes
    or out, making the next character itself, or with `c_escapes` the C escape it begins. Any
    other escape fails the test, since systemd refuses it or reads it as something else."""
    words: list[str] = []
    word: list[str] = []
    started, quote = False, ""
    chars = iter(value)
    for char in chars:
        if char == "\\":
            char = next(chars, "")
            assert char, f"{value!r} ends in a backslash"
            if c_escapes and char not in "\\\"'":
                assert char in C_ESCAPES, (
                    f"{value!r}: systemd refuses \\{char} or reads it otherwise"
                )
                char = C_ESCAPES[char]
            word.append(char)
            started = True
        elif quote:
            if char == quote:
                quote = ""
            else:
                word.append(char)
        elif char in " \t":
            if started:
                words.append("".join(word))
                word, started = [], False
        elif char in "\"'":
            quote, started = char, True
        else:
            word.append(char)
            started = True
    assert not quote, f"{value!r} opens a quote it never closes"
    if started:
        words.append("".join(word))
    return words


def systemd_reads(unit: str) -> dict[str, list[str]]:
    """What systemd takes from each directive of `unit` the wizard fills in, by its own rules: a
    line ending in a backslash goes on to the next, and a value loses the spaces at either end
    (systemd.syntax(5)); `User=` and `WorkingDirectory=` take the rest of the line whole, the
    directory losing a slash at its end as systemd tidies a path; `EnvironmentFile=` takes a glob
    pattern, whose backslashes glob(7) takes away; `Environment=` and `ExecStart=` split into
    words with C escapes, `ReadWritePaths=` without; and in every one a `%%` is a `%`, and any
    other `%` a specifier systemd would expand, which fails the test (systemd.unit(5))."""

    def specified(word: str) -> str:
        assert "%" not in word.replace("%%", ""), f"{word!r} has a % systemd would expand"
        return word.replace("%%", "%")

    said: dict[str, list[str]] = {}
    for line in unit.splitlines():
        if not line or line.startswith(("#", "[")):
            continue
        assert (len(line) - len(line.rstrip("\\"))) % 2 == 0, f"{line!r} goes on to the next line"
        key, _, value = line.partition("=")
        value = value.strip(" \t")
        if key == "User":
            words = [specified(value)]
        elif key == "WorkingDirectory":
            words = [specified(value).rstrip("/")]
        elif key == "EnvironmentFile":
            words = [re.sub(r"\\(.)", r"\1", specified(value.removeprefix("-")))]
        elif key in ("Environment", "ExecStart", "ReadWritePaths"):
            split = systemd_words(value, c_escapes=key != "ReadWritePaths")
            words = [specified(word) for word in split]
        else:
            continue
        said.setdefault(key, []).extend(words)
    return said


#: Robot directories as a Linux robot may name them, each holding what systemd's reading of a
#: unit changes: a space, which splits a list; a %, which begins a specifier; the quotes and the
#: backslash its quoting takes away; glob's pattern characters, which EnvironmentFile= reads;
#: and a space or a backslash at the end, which a line loses, or carries on to the next.
ODD_DIRECTORIES = (
    "/home/rok/robot one",
    "/srv/robot%h",
    '/srv/it\'s "the" robot',
    "/srv/back\\new robot",
    "/srv/back\\new",
    "/srv/robot[1]*?",
    "/srv/robot ",
    "/srv/robot\\",
)


@pytest.mark.parametrize("where", ODD_DIRECTORIES)
def test_systemd_reads_each_directory_program_and_user_in_the_unit_back_as_it_was_given(
    where: str,
) -> None:
    """The robot directory, roboglot's own path and the user went into the unit as they were,
    and systemd splits `Environment=`, `ReadWritePaths=` and `ExecStart=` at a space, takes
    quotes and backslashes away, expands a `%` everywhere, reads `EnvironmentFile=` as a pattern
    and drops a space that ends a line: a directory with a space had the service run on another
    directory while the wizard said it was installed. Each value is read back here by systemd's
    own rules (`systemd_reads`), for a Linux robot's path on any system."""
    program = '/opt/ro boglot %i/"it\'s"\\bin/roboglot'
    user = "rok%h"
    text = systemd.unit_text(user=user, directory=PurePosixPath(where), roboglot=program)
    assert systemd_reads(text) == {
        "User": [user],
        "WorkingDirectory": [where],
        "Environment": [
            "PYTHONUNBUFFERED=1",
            f"ROBOGLOT_ONBOARD_DIR={where}",
            f"ROBOGLOT_MEMORY_DIR={where}/memory",
        ],
        "EnvironmentFile": [f"{where}/robot.env"],
        "ExecStart": [program, "onboard", "serve"],
        "ReadWritePaths": [where],
    }


@pytest.mark.parametrize("held", ["\n", "\r", "\t", "\x00", "\x1b", "\x7f", "\x85", "\udcff"])
@pytest.mark.parametrize("what", ["the robot directory", "the user", "roboglot's own path"])
def test_a_directory_user_or_program_no_unit_can_hold_is_refused_naming_which(
    what: str, held: str
) -> None:
    """A newline in any of the three began a directive of its own, `ExecStartPre=+` run as root
    among them, in a unit installed with sudo whose text the wizard never shows. No quoting
    carries a control character through systemd's reading, nor a name's bytes that are not
    UTF-8, so the unit is refused, naming which value holds it."""
    given = {
        "the robot directory": "/srv/robot",
        "the user": "rok",
        "roboglot's own path": "/usr/bin/roboglot",
    }
    given[what] += held + "ExecStartPre=+/bin/true"
    with pytest.raises(ValueError, match=f"^{re.escape(what)} "):
        systemd.unit_text(
            user=given["the user"],
            directory=PurePosixPath(given["the robot directory"]),
            roboglot=given["roboglot's own path"],
        )


@pytest.mark.parametrize("user", ["", " rok", "rok ", "rok\\"])
def test_a_user_name_that_user_would_read_as_another_or_as_root_is_refused(user: str) -> None:
    """`User=` takes one name and no quotes: systemd drops a space at either end, which makes it
    another account, reads a backslash at the end of the line as the line going on, and an
    empty `User=` runs the service as root. Each is refused rather than written."""
    with pytest.raises(ValueError, match=r"^the user"):
        systemd.unit_text(user=user, directory=PurePosixPath("/srv/robot"), roboglot="/usr/bin/x")


def runs_as(monkeypatch: pytest.MonkeyPatch, name: str | None, *, uid: int = 1000) -> None:
    """The wizard runs with effective uid `uid`, which a stand-in for the system's account
    database names `name`, or knows no account for when `name` is None. Windows has no `pwd` and
    no `os.geteuid`, and on Linux the suite never asks the real database who runs it."""

    def getpwuid(asked: int) -> SimpleNamespace:
        if name is None or asked != uid:
            raise KeyError(f"getpwuid(): uid not found: {asked}")
        return SimpleNamespace(pw_name=name, pw_uid=asked)

    monkeypatch.setattr(os, "geteuid", lambda: uid, raising=False)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwuid=getpwuid))


@pytest.mark.parametrize(
    ("what", "where", "program"),
    [
        ("the robot directory", "/srv/robot\nExecStartPre=+/bin/true", "/usr/bin/roboglot"),
        ("roboglot's own path", "/srv/robot", "/usr/bin/roboglot\nExecStartPre=+/bin/true"),
    ],
)
def test_a_unit_that_cannot_hold_its_directory_or_program_is_refused_before_anything_is_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, what: str, where: str, program: str
) -> None:
    """The wizard staged the unit and ran its sudo commands with whatever the robot directory or
    roboglot's own path held, so a newline in either installed a directive of its own. It is
    refused now, naming which, before a file is staged or a command runs."""
    runs_as(monkeypatch, "rok")
    monkeypatch.setattr(wizard.shutil, "which", lambda _name: program)
    staged: list[str] = []

    def mkdtemp(*_args: object, **_kwargs: object) -> str:
        staged.append("a folder for the unit")
        return str(tmp_path)

    monkeypatch.setattr(wizard.tempfile, "mkdtemp", mkdtemp)
    ran: list[list[str]] = []
    with pytest.raises(wizard.WizardError, match=f"^{re.escape(what)} "):
        wizard.install_service(
            SimpleNamespace(root=PurePosixPath(where)),
            say=lambda _line: None,
            ask=NoQuestions(),
            run=lambda command: ran.append(command) or 0,
        )
    assert (staged, ran, list(tmp_path.iterdir())) == ([], [], [])


@pytest.mark.parametrize(
    ("user", "logname"),
    [(None, None), ("someoneelse", None), (None, "someoneelse"), ("robotuser", None)],
    ids=["no USER or LOGNAME", "a stale USER", "a stale LOGNAME", "USER naming the person"],
)
def test_the_unit_runs_as_the_account_whose_uid_runs_the_wizard_whatever_user_says(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    user: str | None,
    logname: str | None,
) -> None:
    """The unit's `User=` came from `USER` or `LOGNAME` and fell back to root, just after the
    wizard had refused a root uid: with neither set it enabled the networked service as root,
    and a stale `USER` named another account. It is the account the effective uid has in the
    account database now, whatever the variables say; the staged unit is read as systemd reads
    it, and the commands that install it are the ones they always were."""
    runs_as(monkeypatch, "robotuser")
    for variable, value in (("USER", user), ("LOGNAME", logname)):
        if value is None:
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, value)
    monkeypatch.setattr(wizard.tempfile, "mkdtemp", lambda *_args, **_kwargs: str(tmp_path))
    ran: list[list[str]] = []
    assert wizard.install_service(
        SimpleNamespace(root=PurePosixPath("/home/robotuser/.roboglot/onboard")),
        say=lambda _line: None,
        ask=NoQuestions(),
        run=lambda command: ran.append(command) or 0,
    )
    unit = (tmp_path / systemd.UNIT_NAME).read_text(encoding="utf-8")
    assert systemd_reads(unit)["User"] == ["robotuser"]
    assert ran == systemd.install_commands(tmp_path / systemd.UNIT_NAME)


@pytest.mark.parametrize(
    ("account", "said"),
    [
        (None, "no account on this system has uid 1000"),
        ("robot\nExecStartPre=+/bin/true", "the user "),
        (False, "this system has no account database"),
    ],
    ids=["no account for the uid", "a name no unit can hold", "no account database"],
)
def test_a_uid_with_no_account_a_unit_can_name_installs_nothing_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, account: str | bool | None, said: str
) -> None:
    """With no account for its uid the wizard wrote `User=root`, or whatever `USER` said, and
    went on. With none to name, or one whose name no unit can hold, it installs nothing now, not
    even a staged file, and its line says why, whatever `USER` says."""
    if account is False:
        monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
        monkeypatch.setitem(sys.modules, "pwd", None)  # importing it fails, as on Windows
    else:
        runs_as(monkeypatch, None if account is None else str(account))
    monkeypatch.setenv("USER", "robotuser")
    staged: list[str] = []

    def mkdtemp(*_args: object, **_kwargs: object) -> str:
        staged.append("a folder for the unit")
        return str(tmp_path)

    monkeypatch.setattr(wizard.tempfile, "mkdtemp", mkdtemp)
    ran: list[list[str]] = []
    with pytest.raises(wizard.WizardError, match=re.escape(said)):
        wizard.install_service(
            SimpleNamespace(root=PurePosixPath("/home/robotuser/.roboglot/onboard")),
            say=lambda _line: None,
            ask=NoQuestions(),
            run=lambda command: ran.append(command) or 0,
        )
    assert (staged, ran, list(tmp_path.iterdir())) == ([], [], [])


@pytest.mark.skipif(sys.platform == "win32", reason="Windows files have no POSIX modes")
def test_the_brains_key_is_written_for_its_user_alone(tmp_path: Path) -> None:
    path = tmp_path / "robot.env"
    wizard.write_key_file(path, "LOCAL_API_KEY", "secret-value")
    assert path.read_text(encoding="utf-8") == "LOCAL_API_KEY=secret-value\n"
    assert (path.stat().st_mode & 0o077) == 0
    with pytest.raises(wizard.WizardError):
        wizard.write_key_file(path, "LOCAL_API_KEY", "two\nlines")
